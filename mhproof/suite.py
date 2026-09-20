"""Live checks with explicit evidence requirements; queueing is never a pass."""
import itertools
import hashlib
import json
from pathlib import Path
import secrets
import shlex
import sys
import time

from . import VERSION
from .contract import INSTRUCTIONS
from .compat import ClaudeChannelLog
from .evidence import native_call, tool_audit
from .relay import State, serve, write_json
from .telemetry import manifest
from .workload import FIXTURE


def case_evidence(events, source, target, case_id, nonce, memory, sessions, busy=False):
    """Return an evidence chain only when both actual agents did their part."""
    messages = [e for e in events if e["event"] == "queued" and
                e["message"].get("case_id") == case_id]
    challenge = next((e for e in messages if e["peer"] == source and
                      e["message"].get("to") == target and e["message"].get("kind") == "challenge" and
                      e["message"].get("nonce") == nonce and e["message"].get("memory") == ""), None)
    reply = next((e for e in messages if e["peer"] == target and
                  e["message"].get("to") == source and e["message"].get("kind") == "reply" and
                  e["message"].get("nonce") == nonce and e["message"].get("memory") == memory), None)
    received = next((e for e in events if e["event"] == "report" and e["peer"] == source and
                     e["report"] == {"phase": "received", "case_id": case_id, "nonce": nonce,
                                     "memory": memory, "peer": target}), None)
    if not (challenge and reply and received):
        return None
    submitted = []
    for msg, recipient in ((challenge, target), (reply, source)):
        hit = next((e for e in events if e["event"] == "submitted" and e["peer"] == recipient and
                    e.get("message_id") == msg["message"]["id"]), None)
        if not hit or hit["seq"] <= msg["seq"]:
            return None
        submitted.append(hit)
    chain = [challenge, submitted[0], reply, submitted[1], received]
    if not challenge["seq"] < reply["seq"] < received["seq"]:
        return None
    native = []
    for event, name, arguments in (
        (challenge, "proof_send", {k: challenge["message"][k] for k in ("to", "kind", "case_id", "nonce", "memory")}),
        (reply, "proof_send", {k: reply["message"][k] for k in ("to", "kind", "case_id", "nonce", "memory")}),
        (received, "proof_report", received["report"]),
    ):
        observed = native_call(events, event["peer"], name, arguments, sessions)
        if not observed:
            return None
        native.append(observed)
    # RPC acknowledgement can arrive after the model has already replied. Its
    # event is an observation time, not a claim about when ingestion occurred.
    timing = {"challenge_ack_after_reply": submitted[0]["seq"] > reply["seq"],
              "reply_ack_after_receipt": submitted[1]["seq"] > received["seq"]}
    if busy:
        start = next((e for e in events if e["event"] == "hold_started" and
                      e["peer"] == target and e.get("case_id") == case_id), None)
        finish = next((e for e in events if e["event"] == "hold_finished" and
                       e["peer"] == target and e.get("case_id") == case_id), None)
        if not start or not finish or not finish.get("released"):
            return None
        if not start["seq"] < submitted[0]["seq"] < finish["seq"]:
            return None
        held_call = native_call(events, target, "proof_hold", {"case_id": case_id}, sessions)
        if not held_call:
            return None
        timing["reply_before_hold_finished"] = reply["seq"] < finish["seq"]
        turn, reply_turn = held_call.get("turn_id"), native[1].get("turn_id")
        timing["reply_in_hold_turn"] = turn == reply_turn if turn and reply_turn else None
        native.append(held_call)
        chain += [start, finish]
    return {"event_seqs": [e["seq"] for e in chain],
            "native_evidence_seqs": [e["seq"] for e in native],
            "native_sessions": {p: sessions[p]["session_id"] for p in (source, target)},
            "timing": timing,
            "challenge_id": challenge["message"]["id"], "reply_id": reply["message"]["id"],
            "seconds": round(received["time"] - challenge["time"], 3)}


def case_diagnostics(events, result, memory):
    """Non-gating breadcrumbs: retain mismatches instead of reducing all to timeout."""
    queued = [e for e in events if e["event"] == "queued" and e["message"].get("case_id") == result["case_id"]]
    challenges = [e for e in queued if e["peer"] == result["source"] and e["message"]["kind"] == "challenge"]
    replies = [e for e in queued if e["peer"] == result["target"] and e["message"]["kind"] == "reply"]
    reports = [e for e in events if e["event"] == "report" and e["peer"] == result["source"] and
               e["report"].get("case_id") == result["case_id"]]
    def submissions(messages, recipient):
        ids = {e["message"]["id"] for e in messages}
        return [e["seq"] for e in events if e["event"] == "submitted" and e["peer"] == recipient and e.get("message_id") in ids]
    return {"challenge_seqs": [e["seq"] for e in challenges],
            "challenge_submission_seqs": submissions(challenges, result["target"]),
            "reply_observations": [{"seq": e["seq"], "nonce_matches": e["message"].get("nonce") == result["nonce"],
                                    "memory_matches": e["message"].get("memory") == memory} for e in replies],
            "reply_submission_seqs": submissions(replies, result["source"]),
            "receipt_observations": [{"seq": e["seq"], "nonce_matches": e["report"].get("nonce") == result["nonce"],
                                      "memory_matches": e["report"].get("memory") == memory} for e in reports],
            "native_tool_seqs": [e["seq"] for e in events if e["event"] == "native_tool" and
                                  isinstance(e.get("arguments"), dict) and e["arguments"].get("case_id") == result["case_id"]]}


class Suite:
    def __init__(self, state, timeout, startup_timeout, work=True, checks=None):
        self.state, self.timeout, self.startup_timeout = state, timeout, startup_timeout
        self.memories = {p: secrets.token_hex(12) for p in state.peers}
        self.results = []
        self.work = work
        self.work_results = []
        self.phase = "initializing"
        self.starting = False
        self.last_startup_progress = None
        self.last_progress_write = 0
        checks = checks or {}
        self.round_trip_pairs = checks.get("round_trip_pairs", list(itertools.permutations(state.peers, 2)))
        self.busy_pairs = checks.get("busy_pairs", [(state.peers[(i + 1) % len(state.peers)], target)
                                                    for i, target in enumerate(state.peers)])
        self.burst_per_peer = checks.get("burst_per_peer", 2)

    def progress(self, events, force=False):
        now = time.monotonic()
        if not force and now - self.last_progress_write < 1:
            return
        self.last_progress_write = now
        write_json(self.state.directory / "progress.json", {
            "phase": self.phase, "updated": time.time(), "cases_planned": self.planned(),
            "cases": self.results, "work": self.work_results,
            "startup": self.startup_evidence(events) if self.starting else {}})

    def planned(self):
        return len(self.round_trip_pairs) + len(self.busy_pairs) + (self.burst_per_peer * len(self.state.peers) if self.work else 0)

    def startup_evidence(self, events):
        result = {}
        for peer, memory in self.memories.items():
            own = [e for e in events if e["peer"] == peer]
            args = {"phase": "ready", "case_id": "startup", "nonce": "", "memory": memory, "peer": ""}
            report = next((e for e in own if e["event"] == "report" and e["report"] == args), None)
            observed = native_call(events, peer, "proof_report", args, self.state.sessions) if peer in self.state.sessions else None
            channel = next((e for e in own if e["event"] == "channel_ready"), None)
            checks = {
                "registered": peer in self.state.sessions,
                "mcp_connected": any(e["event"] == "mcp_ready" for e in own) if peer != "codex" else None,
                "channel_handler_registered": bool(channel) if peer == "claude" else None,
                "instruction_submitted": any(e["event"] == "submitted" and e.get("case_id") == "startup" for e in own),
                "ready_report": bool(report), "native_ready_call": bool(observed)}
            result[peer] = {**checks, "missing": [name for name, value in checks.items() if value is False],
                "ready_report_seq": report["seq"] if report else None,
                "native_ready_seq": observed["seq"] if observed else None,
                "channel_registration_seq": channel["seq"] if channel else None}
        return result

    def wait(self, predicate, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            if (self.state.directory.parent / "stop.request").exists() and \
                    (self.state.directory.parent / "session.json").exists():
                raise KeyboardInterrupt()
            events = self.state.snapshot()
            self.progress(events)
            errors = [e for e in events if e["event"] in ("adapter_error", "delivery_error", "unsupported", "adapter_stopped",
                                                        "native_identity_error", "tool_violation", "permission_denied")]
            if errors:
                useful = [e for e in errors if e["event"] != "adapter_stopped"]
                raise RuntimeError(json.dumps((useful or errors)[-1]))
            changed = [e for e in events if e["event"] in {"session_observed", "native_session", "native_tool"} and
                       e["peer"] in self.state.sessions and
                       e.get("native_id") != self.state.sessions[e["peer"]]["session_id"]]
            if changed:
                raise RuntimeError("Observed native session identity differs from registration: " + changed[-1]["peer"])
            if self.starting:
                progress = {"phase": self.phase, "peers": self.startup_evidence(events)}
                if progress != self.last_startup_progress:
                    self.last_startup_progress = progress
                    self.state.emit("startup_progress", **progress)
                    summary = "; ".join(peer + ": " + (", ".join(info["missing"]) or "ready evidence matched")
                                        for peer, info in progress["peers"].items())
                    print("Startup [" + self.phase + "] — " + summary, flush=True)
            value = predicate(events)
            if value:
                return value
            time.sleep(0.1)
        events = self.state.snapshot()
        self.state.emit("verification_timeout", phase=self.phase,
                        timeout_seconds=self.timeout if timeout is None else timeout,
                        activity={peer: next((e for e in reversed(events) if e["event"] == "activity" and
                                             e["peer"] == peer), None) for peer in self.state.peers},
                        tool_audit=tool_audit(events, self.state.sessions),
                        startup=self.startup_evidence(events) if self.starting else None)
        raise TimeoutError("No complete evidence during " + self.phase +
                           "; inspect native observations, tool permissions, and delivery traces")

    def settled(self):
        # Claude: run-local Stop hook. Codex: native turn events. Grok: native
        # completion plus outstanding requests/interjections; never an invented start.
        def idle(events):
            for peer in self.state.peers:
                activity = [e for e in events if e["peer"] == peer and e["event"] == "activity"]
                if not activity or activity[-1]["state"] != "idle":
                    return False
            return True
        self.wait(idle)

    def startup(self):
        self.starting = True
        self.phase = "participant registration and MCP connection"
        def connected(events):
            return all(p in self.state.sessions for p in self.state.peers) and all(
                p == "codex" or any(e["event"] == "mcp_ready" and e["peer"] == p for e in events)
                for p in self.state.peers)
        self.wait(connected, self.startup_timeout)
        if "claude" in self.state.peers:
            self.phase = "Claude channel handler registration (claude-debug.log)"
            channel_log = ClaudeChannelLog(self.state.directory / "claude-debug.log")
            def channel_ready(events):
                observed = channel_log.poll()
                if observed:
                    self.state.emit("channel_ready", "claude", **observed)
                    return True
                return False
            self.wait(channel_ready)
        self.phase = "native channel/tool readiness (ready report and matching native tool observation)"
        for peer, memory in self.memories.items():
            prompt = (INSTRUCTIONS + "\nYour peer identity is " + peer + ".\nPRIVATE_MEMORY=" + memory +
                      '\nCall proof_report with phase="ready", case_id="startup", nonce="", '
                      'memory=your PRIVATE_MEMORY, peer="". Then end this turn.')
            self.state.control(peer, prompt, "startup")
        def ready(events):
            for peer, memory in self.memories.items():
                args = {"phase": "ready", "case_id": "startup", "nonce": "", "memory": memory, "peer": ""}
                if not any(e["event"] == "report" and e["peer"] == peer and e["report"] == args for e in events):
                    return False
                if not native_call(events, peer, "proof_report", args, self.state.sessions):
                    return False
            return True
        self.wait(ready)
        self.phase = "startup native completion"
        self.settled()
        self.starting = False
        print("All peers ready in their persistent native sessions.", flush=True)

    def case(self, source, target, busy):
        kind = "busy" if busy else "round-trip"
        case_id = f"{kind}-{source}-to-{target}"
        nonce = secrets.token_hex(12)
        result = {"case_id": case_id, "source": source, "target": target,
                  "check": kind, "status": "unverified", "nonce": nonce}
        self.results.append(result)
        self.phase = case_id
        print("Running " + case_id, flush=True)
        try:
            self.settled()
            if busy:
                self.state.control(target, f'Call proof_hold with case_id="{case_id}" now.', case_id)
                self.wait(lambda events: any(e["event"] == "hold_started" and e["peer"] == target and
                          e.get("case_id") == case_id for e in events))
            self.state.control(source, 'Call proof_send once with ' + json.dumps({
                "to": target, "kind": "challenge", "case_id": case_id,
                "nonce": nonce, "memory": ""}) + ". Then end this turn. Handle the eventual reply normally.", case_id)
            if busy:
                def delivered(events):
                    ids = {e["message"]["id"] for e in events if e["event"] == "queued" and
                           e["peer"] == source and e["message"].get("case_id") == case_id and
                           e["message"].get("kind") == "challenge"}
                    return any(e["event"] == "submitted" and e["peer"] == target and
                               e.get("message_id") in ids for e in events)
                try:
                    self.wait(delivered)
                finally:
                    self.state.release(target, case_id)
            evidence = self.wait(lambda events: case_evidence(
                events, source, target, case_id, nonce, self.memories[target], self.state.sessions, busy))
            result.update(status="pass", **evidence)
            print("PASS " + case_id, flush=True)
        except Exception as e:
            errors = [x for x in self.state.snapshot() if x["event"] == "unsupported"]
            result.update(status="unsupported" if errors else "unverified", detail=str(e))
            raise

    def run(self):
        self.startup()
        for source, target in self.round_trip_pairs:
            self.case(source, target, busy=False)
        for source, target in self.busy_pairs:
            self.case(source, target, busy=True)
        if self.work:
            self.work_stress()
        self.phase = "final native completion and tool audit"
        self.settled()
        self.wait(lambda events: tool_audit(events, self.state.sessions)["status"] == "pass")

    def work_stress(self):
        self.settled()
        print(f"Running build-log work fixture with a {self.burst_per_peer}-message burst per peer", flush=True)
        plans = {}
        checks = []
        for i, source in enumerate(self.state.peers):
            target = self.state.peers[(i + 1) % len(self.state.peers)]
            plans[source] = [{"to": target, "kind": "challenge", "case_id": f"work-{source}-to-{target}-{j}",
                              "nonce": secrets.token_hex(12), "memory": ""} for j in range(self.burst_per_peer)]
            for challenge in plans[source]:
                result = {"case_id": challenge["case_id"], "source": source, "target": target,
                          "check": "message-during-work", "status": "unverified", "nonce": challenge["nonce"]}
                self.results.append(result)
                checks.append((result, challenge))
        for peer in self.state.peers:
            self.work_results.append({"peer": peer, "status": "unverified"})
        for batch in range(len(self.state.workload.batches)):
            self.phase = "work batch " + str(batch)
            if batch:
                with self.state.lock:
                    self.state.workload.release_batch(batch)
            for peer in self.state.peers:
                prompt = (
                    "Solve exactly ONE build-log batch using proof_work_next and proof_work_submit. "
                    "Consider only the highest numbered attempt of each job. Report the IDs whose latest "
                    "status is FAIL and the sum of failed_tests for those jobs. Ignore WARN/PASS and "
                    "superseded attempts. After submitting, END THIS TURN and await the controller. "
                    "Do not fetch later batches or read fixture files. Handle peer messages when available.\n"
                    "WORK_BATCH=" + str(batch))
                self.state.control(peer, prompt, "work-batch-" + str(batch))
            self.wait(lambda events: all(any(e["event"] == "work_scored" and e["peer"] == p and
                      e["batch"] == batch for e in events) for p in self.state.peers), timeout=self.timeout * 3)
            self.settled()
            if batch == 0:
                # Every peer has unfinished work. Later batches are gated until
                # every burst is submitted; model speed cannot eliminate overlap.
                self.phase = "work burst submission barrier"
                for peer in self.state.peers:
                    self.state.control(peer, "Send each challenge below via proof_send exactly once. "
                        "Then end this turn.\nWORK_PLAN_JSON=" + json.dumps(plans[peer]), "work-burst")
                def burst_submitted(events):
                    for result, challenge in checks:
                        queued = [e for e in events if e["event"] == "queued" and e["peer"] == result["source"] and
                                  e["message"].get("case_id") == challenge["case_id"] and
                                  e["message"].get("kind") == "challenge"]
                        if not any(e["event"] == "submitted" and e["peer"] == result["target"] and
                                   any(e.get("message_id") == q["message"]["id"] for q in queued) for e in events):
                            return False
                    return True
                self.wait(burst_submitted)
                self.state.emit("work_burst_submitted")
                self.settled()
        self.phase = "work message verification"
        self.wait(self.refresh_work_evidence)
        if any(r["status"] != "pass" for r in self.work_results + [x[0] for x in checks]):
            raise RuntimeError("Work accuracy or message/work overlap was not fully verified; see per-case evidence")
        print("PASS build-log accuracy and all message/work overlaps", flush=True)

    def refresh_work_evidence(self, events):
        for result in self.work_results:
            scores = [e for e in events if e["event"] == "work_scored" and e["peer"] == result["peer"]]
            result["scores"] = [{k: e[k] for k in ("batch", "submitted", "expected", "correct")} for e in scores]
            completed = next((e for e in events if e["event"] == "work_completed" and e["peer"] == result["peer"]), None)
            if completed:
                result.update(status="pass" if completed["correct"] else "incorrect", evidence_seq=completed["seq"],
                              batches=completed["batches"])
        checks = [r for r in self.results if r["check"] == "message-during-work"]
        for result in checks:
            source, target = result["source"], result["target"]
            evidence = case_evidence(events, source, target, result["case_id"],
                                    result["nonce"], self.memories[target], self.state.sessions)
            start = next((e for e in events if e["event"] == "work_started" and e["peer"] == target), None)
            finish = next((e for e in events if e["event"] == "work_completed" and e["peer"] == target), None)
            if not evidence or not start or not finish:
                continue
            submitted = next(e for e in events if e["event"] == "submitted" and
                             e.get("message_id") == evidence["challenge_id"] and e["peer"] == target)
            overlap = start["seq"] < submitted["seq"] < finish["seq"]
            reply = next(e for e in events if e["event"] == "queued" and
                         e["message"].get("id") == evidence["reply_id"])
            evidence["timing"]["reply_before_work_completed"] = reply["seq"] < finish["seq"]
            result.update(status="pass" if overlap else "unverified", work_overlap=overlap, **evidence)
            if not overlap:
                result["detail"] = "Round trip succeeded, but submission did not overlap recipient work"
        return all("event_seqs" in r for r in checks)


def run_suite(directory, peers, timeout=180, startup_timeout=600, work=True, mode="live", settings=None):
    if mode not in {"live", "mock"}:
        raise ValueError("Unknown evidence mode")
    directory = Path(directory).resolve()
    # A failed run remains inspectable; never overwrite or silently resume it.
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    settings = settings or {}
    fixture = Path(settings.get("fixture", FIXTURE))
    # Copy the selected input, so later source/config edits cannot change this run.
    fixture_copy = directory / "fixture.json"
    write_json(fixture_copy, json.loads(fixture.read_text()))
    state = State(directory, peers, hold_timeout=timeout + 15, fixture=fixture_copy)
    server = serve(state)
    write_json(directory / "run.json", {"version": VERSION, "mode": mode,
               "url": f"http://127.0.0.1:{server.server_address[1]}",
               "tokens": state.tokens, "peers": peers, "timeout": timeout,
               "agent_settings": settings.get("agents", {})})
    if settings:
        write_json(directory / "settings.json", settings)
    write_json(directory / "manifest.json", {**manifest(Path(__file__).resolve().parents[1], peers, timeout), "mode": mode,
               "fixture_sha256": hashlib.sha256(fixture_copy.read_bytes()).hexdigest(),
               "fixture_batches": len(state.workload.batches),
               "fixture_rows": sum(len(batch) for batch in state.workload.batches)})
    suite = Suite(state, timeout, startup_timeout, work=work, checks=settings.get("checks"))
    report = {"version": VERSION, "mode": mode, "status": "unverified", "peers": list(peers),
              "started": time.time(), "cases": suite.results, "work": suite.work_results,
              "settings": {"timeout": timeout, "startup_timeout": startup_timeout, "work": work,
                           "hold_timeout": timeout + 15, "grok_prompt_timeout": 3 * timeout + 30,
                           "round_trip_pairs": suite.round_trip_pairs, "busy_pairs": suite.busy_pairs,
                           "burst_per_peer": suite.burst_per_peer, "agents": settings.get("agents", {})},
              "limits": ["Busy means a native proof_hold tool call was outstanding when delivery was submitted.",
                         "A pass does not prove interruption during token generation or arbitrary tools.",
                         "Claude completion is observed through run-local hooks; Grok combines native completion with pending deliveries.",
                         "Work overlap means an unfinished fixture, with later batches gated during the burst; it does not prove concurrent computation.",
                         "This tests newly launched persistent sessions, not attachment to unrelated existing TUIs.",
                         "Context checks test cooperation, not adversarial isolation or access control."]}
    print("\nOpen one terminal per peer and run:", flush=True)
    entry = Path(__file__).resolve().parents[1] / "proof.py"
    for peer in peers:
        print("  " + shlex.join([sys.executable, str(entry), "agent", peer, "--dir", str(directory)]), flush=True)
    print(f"\nWaiting up to {startup_timeout}s. Results: {directory / 'report.json'}\n", flush=True)
    write_json(directory / "report.json", report)
    try:
        suite.run()
        report["status"] = "pass"
    except KeyboardInterrupt:
        report["detail"] = "Interrupted by operator"
        state.emit("verification_failed", phase=suite.phase, detail=report["detail"])
    except Exception as e:
        report["detail"] = str(e)
        state.emit("verification_failed", phase=suite.phase, detail=report["detail"])
        print("UNVERIFIED: " + str(e), flush=True)
    finally:
        state.close()
        events = state.snapshot()
        suite.refresh_work_evidence(events)
        for result in suite.results:
            if result["status"] != "pass":
                result["diagnostics"] = case_diagnostics(events, result, suite.memories[result["target"]])
        report.update(finished=time.time(), sessions=state.sessions, phase=suite.phase,
                      startup=suite.startup_evidence(events),
                      tool_audit=tool_audit(events, state.sessions),
                      evidence_last_seq=events[-1]["seq"] if events else 0,
                      capability_probes=[e for e in events if e["event"] == "protocol_capability"])
        if report["status"] == "pass" and report["tool_audit"]["status"] != "pass":
            report.update(status="unverified", detail="Final tool audit did not pass")
        planned = suite.planned()
        report["cases_planned"] = planned
        report["cases_not_run"] = planned - len(suite.results)
        write_json(directory / "report.json", report)
        suite.progress(events, force=True)
        print("\n" + report["status"].upper() + ": " + str(directory / "report.json"), flush=True)
        print("Close the Claude terminal with /exit when finished. Codex/Grok wrappers exit automatically.", flush=True)
        # Give polling adapters one opportunity to see stopped, then close the
        # listener. No retries or session restart can accidentally pass a case.
        time.sleep(2)
        server.shutdown()
        server.server_close()
        state.log.close()
    return 0 if report["status"] == "pass" else 1
