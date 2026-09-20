"""Readable run summaries; verdicts still come from the evidence verifier."""
import json
from pathlib import Path


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def events_from(directory):
    path = Path(directory) / "events.jsonl"
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # A writer may still be flushing the last line.
    return result


def summarize(directory):
    directory = Path(directory)
    report = read_json(directory / "report.json", {})
    progress = read_json(directory / "progress.json", {})
    finished = "finished" in report
    cases = report.get("cases", []) if finished else progress.get("cases", [])
    work = report.get("work", []) if finished else progress.get("work", [])
    result = {"mode": report.get("mode", "unknown"),
              "status": report.get("status", "unverified") if finished else "running",
              "finished": finished, "phase": report.get("phase") if finished else progress.get("phase", "launching"),
              "message_passes": sum(c.get("status") == "pass" for c in cases),
              "message_total": report.get("cases_planned", progress.get("cases_planned", 0)),
              "cases": [{k: c[k] for k in ("case_id", "status", "detail", "diagnostics") if k in c} for c in cases],
              "work": work, "tool_audit": report.get("tool_audit", {}),
              "detail": report.get("detail"), "startup": report.get("startup", progress.get("startup", {})),
              "report": str(directory / "report.json"), "accuracy_failures": [],
              "pending_permissions": [], "agents": report.get("settings", {}).get("agents", {}),
              "transcript": read_json(directory / "claude-transcript-capture.json", {})}
    pending = {}
    for event in events_from(directory):
        if event["event"] == "work_scored" and not event["correct"]:
            result["accuracy_failures"].append({k: event[k] for k in ("peer", "batch", "submitted", "expected")})
        if event["event"] == "permission_requested":
            pending[(event.get("peer"), event.get("call_id"))] = event
        elif event["event"] == "permission_resolved":
            pending.pop((event.get("peer"), event.get("call_id")), None)
    result["pending_permissions"] = sorted({key[0] for key in pending if key[0]})
    if finished:
        result["seconds"] = round(report["finished"] - report["started"], 1)
    return result


def render(summary):
    lines = [f"{summary['mode'].upper()} {summary['status'].upper()}",
             f"Phase: {summary.get('phase') or 'unknown'}",
             f"Message checks: {summary['message_passes']}/{summary['message_total']} passed"]
    if summary.get("detail"):
        lines.append("Reason: " + summary["detail"])
    if summary.get("seconds") is not None:
        lines.append(f"Elapsed: {summary['seconds']} seconds")
    for peer, settings in summary.get("agents", {}).items():
        lines.append(f"{peer}: model={settings.get('model') or 'harness default'}, "
                     f"reasoning={settings.get('reasoning') or 'harness default'}")
    for case in summary["cases"]:
        if case["status"] != "pass":
            diag = case.get("diagnostics", {})
            stage = "no challenge recorded"
            if diag.get("challenge_seqs"):
                stage = "challenge queued; no submission recorded"
            if diag.get("challenge_submission_seqs"):
                stage = "submitted; no reply recorded"
            if diag.get("reply_observations"):
                stage = "reply recorded; incomplete receipt/native/overlap evidence"
            lines.append(f"  {case['case_id']}: {case['status']} ({stage})")
    for work in summary["work"]:
        lines.append(f"Work {work['peer']}: {work['status']}")
    for failure in summary["accuracy_failures"]:
        lines.append(f"  {failure['peer']} batch {failure['batch']} (zero-based): "
                     f"submitted {json.dumps(failure['submitted'], sort_keys=True)}; "
                     f"expected {json.dumps(failure['expected'], sort_keys=True)}")
    if summary["tool_audit"]:
        audit = summary["tool_audit"]
        lines.append(f"Native tool audit: {audit.get('status')} "
                     f"({audit.get('unmatched_relay_calls', '?')} unmatched relay calls)")
    if not summary["finished"]:
        for peer, item in summary.get("startup", {}).items():
            if item.get("missing"):
                lines.append(peer + " startup: " + ", ".join(item["missing"]))
        for peer in summary["pending_permissions"]:
            lines.append("ACTION: permission requested in the " + peer + " window")
    if summary["transcript"]:
        lines.append("Claude transcript capture: " + summary["transcript"].get("status", "unknown"))
    lines.append("Report: " + summary["report"])
    return "\n".join(lines) + "\n"
