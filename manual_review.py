"""Build an offline manual-review page from the frozen audit comparison."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "reports/audit-replay-20261008T160527-b7a627fe-luna6-xhigh/comparison.json"


def build(source: Path, output: Path | None = None, review: Path | None = None) -> Path:
    raw = source.read_bytes()
    comparison = json.loads(raw)
    items = comparison["items"]
    ids = [item["id"] for item in items]
    if not items or len(ids) != len(set(ids)):
        raise ValueError("The audit must contain unique item IDs.")
    date = min(datetime.fromisoformat(item["date"]) for item in items)
    title = date.astimezone(ZoneInfo("UTC")).strftime("%B %Y")
    payload = {
        "schema_version": 1,
        "dataset_id": source.parent.name,
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "title": title,
        "items": items,
        "duplicate_groups": comparison.get("duplicate_groups", []),
        "notice": comparison.get("notice", ""),
    }
    if review is not None:
        backup = json.loads(review.read_text())
        if (backup.get("schema_version") != 1 or backup.get("dataset_sha256") != payload["dataset_sha256"]
                or len(backup.get("items", [])) != len(items)
                or {row["id"] for row in backup["items"]} != set(ids)):
            raise ValueError("Initial review does not match the report.")
        initial = {"version": 1, "dataset_sha256": payload["dataset_sha256"],
                   "decisions": {}, "drafts": {}, "history": [], "cursor": backup.get("cursor_id")}
        for row in backup["items"]:
            status, decision, note = row["review_status"], row["final_decision"], row["note"]
            if (status not in {"decided", "later", "unreviewed"}
                    or not isinstance(note, str) or len(note) > 3000
                    or status == "decided" and decision not in {"keep", "drop", "duplicate"}
                    or status != "decided" and decision is not None):
                raise ValueError("Invalid initial review decision.")
            target = row.get("duplicate_of")
            if target is not None and (target not in ids or target == row["id"]):
                raise ValueError("Invalid initial duplicate reference.")
            target = target if decision == "duplicate" else None
            initial["drafts"][row["id"]] = {"note": note, "duplicate_of": target}
            if status != "unreviewed":
                initial["decisions"][row["id"]] = {
                    "decision": decision if status == "decided" else "later", "note": note,
                    "duplicate_of": target, "decided_at": row.get("decided_at") or backup["exported_at"]}
        payload["initial_state"] = initial
    # Source posts remain data, even if they contain closing script tags.
    embedded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    embedded = embedded.replace(">", "\\u003e").replace("&", "\\u0026")
    embedded = embedded.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (ROOT / "manual_review.html").read_text()
    output = output or source.parent / "manual-review.html"
    output.write_text(template.replace("__REVIEW_DATA__", embedded))
    return output.resolve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--review", type=Path, help="Matching exported review to preload on first opening")
    args = parser.parse_args()
    print(build(args.source, args.output, args.review))
