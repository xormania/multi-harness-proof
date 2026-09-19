"""Small build-log triage task with retries and a deterministic answer key."""
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "build-log.json"


def read_fixture(path=FIXTURE):
    value = json.loads(Path(path).read_text())
    batches = value.get("batches") if isinstance(value, dict) else None
    if not isinstance(batches, list) or len(batches) < 2:
        raise ValueError("Fixture must contain at least two batches for work overlap")
    for batch in batches:
        if not isinstance(batch, list) or not batch:
            raise ValueError("Fixture batches must be nonempty row lists")
        attempts = set()
        for row in batch:
            if not isinstance(row, dict) or not isinstance(row.get("job"), str) or not row["job"] or \
                    type(row.get("attempt")) is not int or row["attempt"] < 1 or \
                    row.get("status") not in {"PASS", "FAIL", "WARN"} or \
                    type(row.get("failed_tests")) is not int or row["failed_tests"] < 0:
                raise ValueError("Invalid fixture row")
            key = (row["job"], row["attempt"])
            if key in attempts:
                raise ValueError("Duplicate job/attempt makes fixture ordering ambiguous")
            attempts.add(key)
    return batches


def expected(rows):
    latest = {}
    for row in rows:
        if row["job"] not in latest or row["attempt"] > latest[row["job"]]["attempt"]:
            latest[row["job"]] = row
    failures = [row for row in latest.values() if row["status"] == "FAIL"]
    return {"failed_jobs": sorted(row["job"] for row in failures),
            "failed_tests": sum(row["failed_tests"] for row in failures)}


class Workload:
    def __init__(self, emit, fixture=FIXTURE):
        self.emit = emit
        self.batches = read_fixture(fixture)
        self.progress = {}
        self.released_batch = 0

    def release_batch(self, index):
        if index != self.released_batch + 1 or index >= len(self.batches):
            raise ValueError("Work batches must be released in order")
        self.released_batch = index
        self.emit("work_batch_released", batch=index)

    def next(self, peer):
        if peer not in self.progress:
            self.progress[peer] = {"index": 0, "pending": False, "correct": True}
            self.emit("work_started", peer)
        progress = self.progress[peer]
        if progress["pending"]:
            raise ValueError("Submit the current batch before requesting another")
        index = progress["index"]
        if index == len(self.batches):
            return {"done": True}
        if index > self.released_batch:
            raise ValueError("End this turn and await the controller's next batch instruction")
        progress["pending"] = True
        result = {"batch": index, "done": False, "rows": self.batches[index]}
        self.emit("work_batch_issued", peer, batch=index, rows=len(result["rows"]))
        return result

    def submit(self, peer, args):
        progress = self.progress.get(peer)
        if not progress or not progress["pending"] or args["batch"] != progress["index"]:
            raise ValueError("No matching outstanding work batch")
        answer = expected(self.batches[args["batch"]])
        correct = sorted(args["failed_jobs"]) == answer["failed_jobs"] and args["failed_tests"] == answer["failed_tests"]
        self.emit("work_scored", peer, batch=args["batch"], submitted=args, expected=answer, correct=correct)
        progress.update(index=progress["index"] + 1, pending=False,
                        correct=progress["correct"] and correct)
        if progress["index"] == len(self.batches):
            self.emit("work_completed", peer, correct=progress["correct"], batches=len(self.batches))
        # Do not expose the answer key to the agent or invite retries until correct.
        return {"recorded": True, "more_batches": progress["index"] < len(self.batches)}
