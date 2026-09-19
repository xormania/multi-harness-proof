"""Live checks with explicit evidence requirements; queueing is never a pass."""
import itertools
import json
from pathlib import Path
import secrets
import shlex
import sys
import time

from . import VERSION
from .contract import INSTRUCTIONS
from .relay import State, serve, write_json
from .telemetry import manifest


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
        if not hit:
            return None
        submitted.append(hit)
    chain = [challenge, submitted[0], reply, submitted[1], received]
    if not challenge["seq"] < reply["seq"] < received["seq"]:
        return None
    for event in chain:
        if event.get("session_id") != sessions[event["peer"]]["session_id"]:
            return None
    if busy:
        start = next((e for e in events if e["event"] == "hold_started" and
                      e["peer"] == target and e.get("case_id") == case_id), None)
        finish = next((e for e in events if e["event"] == "hold_finished" and
                       e["peer"] == target and e.get("case_id") == case_id), None)
        if not start or not finish or not finish.get("released"):
            return None
        if not start["seq"] < submitted[0]["seq"] < finish["seq"]:
            return None
        chain += [start, finish]
    return {"event_seqs": [e["seq"] for e in chain],
            "challenge_id": challenge["message"]["id"], "reply_id": reply["message"]["id"],
            "seconds": round(received["time"] - challenge["time"], 3)}


class Suite:
    def __init__(self, state, timeout, startup_timeout, work=True):
        self.state, self.timeout, self.startup_timeout = state, timeout, startup_timeout
        self.memories = {p: secrets.token_hex(12) for p in state.peers}
        self.results = []
        self.work = work
        self.work_results = []

    def wait(self, predicate, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            events = self.state.snapshot()
            errors = [e for e in events if e["event"] in ("adapter_error", "delivery_error", "unsupported", "adapter_stopped")]
            if errors:
                useful = [e for e in errors if e["event"] != "adapter_stopped"]
                raise RuntimeError(json.dumps((useful or errors)[-1]))
            changed = [e for e in events if e["event"] == "session_observed" and
                       e["peer"] in self.state.sessions and
                       e["native_id"] != self.state.sessions[e["peer"]]["session_id"]]
            if changed:
                raise RuntimeError("Claude's observed session differs from its launch session")
            value = predicate(events)
            if value:
                return value
            time.sleep(0.1)
        raise TimeoutError("No complete evidence within the configured timeout")

    def settled(self):
        # Codex/Grok expose native turn boundaries. Claude channels expose no
        # idle-state event; its quiet interval is an assumption, recorded below.
        def idle(events):
            for peer in self.state.peers:
                turns = [e for e in events if e["peer"] == peer and
                         e["event"] in ("turn_started", "turn_completed")]
                if turns and turns[-1]["event"] == "turn_started":
                    return False
            return True
        self.wait(idle)
        if "claude" in self.state.peers:
            time.sleep(2)

    def startup(self):
        def connected(events):
            return all(p in self.state.sessions for p in self.state.peers) and all(
                p == "codex" or any(e["event"] == "mcp_ready" and e["peer"] == p for e in events)
                for p in self.state.peers)
        self.wait(connected, self.startup_timeout)
        for peer, memory in self.memories.items():
            prompt = (INSTRUCTIONS + "\nYour peer identity is " + peer + ".\nPRIVATE_MEMORY=" + memory +
                      '\nCall proof_report with phase="ready", case_id="startup", nonce="", '
                      'memory=your PRIVATE_MEMORY, peer="". Then end this turn.')
            self.state.control(peer, prompt, "startup")
        self.wait(lambda events: all(any(e["event"] == "report" and e["peer"] == p and
                  e["report"] == {"phase": "ready", "case_id": "startup", "nonce": "",
                                  "memory": m, "peer": ""} for e in events)
                  for p, m in self.memories.items()))
        self.settled()
        print("All peers ready in their persistent native sessions.", flush=True)

    def case(self, source, target, busy):
        kind = "busy" if busy else "round-trip"
        case_id = f"{kind}-{source}-to-{target}"
        nonce = secrets.token_hex(12)
        result = {"case_id": case_id, "source": source, "target": target,
                  "check": kind, "status": "unverified"}
        self.results.append(result)
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
        for source, target in itertools.permutations(self.state.peers, 2):
            self.case(source, target, busy=False)
        for i, target in enumerate(self.state.peers):
            self.case(self.state.peers[(i + 1) % len(self.state.peers)], target, busy=True)
        if self.work:
            self.work_stress()

    def work_stress(self):
        self.settled()
        print("Running build-log work fixture with a two-message burst per peer", flush=True)
        plans = {}
        checks = []
        for i, source in enumerate(self.state.peers):
            target = self.state.peers[(i + 1) % len(self.state.peers)]
            plans[source] = [{"to": target, "kind": "challenge", "case_id": f"work-{source}-to-{target}-{j}",
                              "nonce": secrets.token_hex(12), "memory": ""} for j in range(2)]
            for challenge in plans[source]:
                result = {"case_id": challenge["case_id"], "source": source, "target": target,
                          "check": "message-during-work", "status": "unverified"}
                self.results.append(result)
                checks.append((result, challenge))
        for peer in self.state.peers:
            self.work_results.append({"peer": peer, "status": "unverified"})
            prompt = (
                "Complete the build-log triage task using only proof tools. Call proof_work_next. "
                "For each batch, consider only the highest numbered attempt of each job. Report the IDs "
                "whose latest status is FAIL, and the sum of failed_tests for those jobs, using "
                "proof_work_submit. Do not count WARN/PASS or superseded attempts. After submitting "
                "your FIRST batch, send each challenge in WORK_PLAN_JSON via proof_send exactly once. "
                "Then fetch and solve the remaining batches until done. Handle incoming peer messages "
                "between batches and continue the work. Do not read fixture files or use a shell.\n"
                "WORK_PLAN_JSON=" + json.dumps(plans[peer]))
            self.state.control(peer, prompt, "work-fixture")
        self.wait(lambda events: all(any(e["event"] == "work_completed" and e["peer"] == p
                  for e in events) for p in self.state.peers), timeout=self.timeout * 3)
        events = self.state.snapshot()
        for result in self.work_results:
            completed = next(e for e in events if e["event"] == "work_completed" and e["peer"] == result["peer"])
            result.update(status="pass" if completed["correct"] else "incorrect", evidence_seq=completed["seq"],
                          batches=completed["batches"])
        for result, challenge in checks:
            source, target = result["source"], result["target"]
            evidence = self.wait(lambda ev: case_evidence(ev, source, target, challenge["case_id"],
                                 challenge["nonce"], self.memories[target], self.state.sessions))
            events = self.state.snapshot()
            submitted = next(e for e in events if e["event"] == "submitted" and
                             e.get("message_id") == evidence["challenge_id"] and e["peer"] == target)
            start = next(e for e in events if e["event"] == "work_started" and e["peer"] == target)
            finish = next(e for e in events if e["event"] == "work_completed" and e["peer"] == target)
            overlap = start["seq"] < submitted["seq"] < finish["seq"]
            result.update(status="pass" if overlap else "unverified", work_overlap=overlap, **evidence)
            if not overlap:
                result["detail"] = "Round trip succeeded, but submission did not overlap recipient work"
        if any(r["status"] != "pass" for r in self.work_results + [x[0] for x in checks]):
            raise RuntimeError("Work accuracy or message/work overlap was not fully verified; see per-case evidence")
        print("PASS build-log accuracy and all message/work overlaps", flush=True)


def run_suite(directory, peers, timeout=180, startup_timeout=600, work=True):
    directory = Path(directory).resolve()
    # A failed run remains inspectable; never overwrite or silently resume it.
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    state = State(directory, peers, hold_timeout=timeout + 15)
    server = serve(state)
    write_json(directory / "run.json", {"version": VERSION,
               "url": f"http://127.0.0.1:{server.server_address[1]}",
               "tokens": state.tokens, "peers": peers, "timeout": timeout})
    write_json(directory / "manifest.json", manifest(Path(__file__).resolve().parents[1], peers, timeout))
    suite = Suite(state, timeout, startup_timeout, work=work)
    report = {"version": VERSION, "mode": "live", "status": "unverified", "peers": list(peers),
              "started": time.time(), "cases": suite.results, "work": suite.work_results,
              "limits": ["Busy means a native proof_hold tool call was outstanding when delivery was submitted.",
                         "A pass does not prove interruption during token generation or arbitrary tools.",
                         "Claude quiescence is inferred after completed proof operations; native idle state is not observed.",
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
    except Exception as e:
        report["detail"] = str(e)
        print("UNVERIFIED: " + str(e), flush=True)
    finally:
        state.close()
        report.update(finished=time.time(), sessions=state.sessions)
        planned = len(peers) * (len(peers) - 1) + len(peers) + (2 * len(peers) if work else 0)
        report["cases_planned"] = planned
        report["cases_not_run"] = planned - len(suite.results)
        write_json(directory / "report.json", report)
        print("\n" + report["status"].upper() + ": " + str(directory / "report.json"), flush=True)
        print("Close the Claude terminal with /exit when finished. Codex/Grok wrappers exit automatically.", flush=True)
        # Give polling adapters one opportunity to see stopped, then close the
        # listener. No retries or session restart can accidentally pass a case.
        time.sleep(2)
        server.shutdown()
        server.server_close()
    return 0 if report["status"] == "pass" else 1
