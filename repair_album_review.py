"""Repair an archived review using verified album metadata, without new AI judgments."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from core import Message, split_message
from manual_review import build


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def repair(comparison_path: Path, parents_path: Path, review_path: Path, metadata_path: Path,
           output: Path, keeps: tuple[str, ...] = ()) -> dict:
    comparison_raw, review_raw = comparison_path.read_bytes(), review_path.read_bytes()
    comparison, review = json.loads(comparison_raw), json.loads(review_raw)
    metadata = json.loads(metadata_path.read_text())
    originals = {row["id"]: row for row in comparison["items"]}
    choices = {row["id"]: row for row in review["items"]}
    if (review["dataset_sha256"] != digest(comparison_raw) or set(choices) != set(originals)
            or len(choices) != len(review["items"]) or not review["complete"]):
        raise ValueError("Complete review must match the original comparison.")
    messages = [Message.from_record(json.loads(line)) for line in parents_path.read_text().splitlines()]
    by_parent = {(m.source.lower(), m.message_id): m for m in messages}
    groups = defaultdict(list)
    verified = set()
    for record in metadata["records"]:
        key = (record["source"].lower(), record["message_id"])
        if key in verified or key not in by_parent:
            raise ValueError("Invalid or repeated metadata record.")
        message = by_parent[key]
        if (digest(message.text.encode()) != record["text_sha256"]
                or type(record["grouped_id"]) is not int):
            raise ValueError("Metadata does not match archived content.")
        message.grouped_id = record["grouped_id"]
        groups[(key[0], message.grouped_id)].append(message)
        verified.add(key)

    aliases, conflicts = {}, {}
    for (source, group_id), members in groups.items():
        captions = [m for m in members if m.text.strip()]
        if len(captions) != 1 or len(split_message(captions[0])) != 1:
            raise ValueError("Historical repair requires one verified caption per album.")
        caption = captions[0]
        canonical = split_message(caption)[0].key
        member_keys = [f"{source}:{m.message_id}:0" for m in sorted(members, key=lambda m: m.message_id)]
        if any(key not in choices for key in member_keys):
            raise ValueError("Album member is missing from the original review.")
        for member in members:
            if member.text.strip():
                continue
            alias = f"{source}:{member.message_id}:0"
            aliases[alias] = {"canonical_id": canonical, "grouped_id": group_id,
                              "original_decision": choices[alias]["final_decision"],
                              "original_decided_at": choices[alias]["decided_at"]}
        if len({choices[key]["final_decision"] for key in member_keys}) > 1:
            conflicts[canonical] = [{"id": key, "decision": choices[key]["final_decision"]}
                                    for key in member_keys]

    retained = {item.key: item for message in messages for item in split_message(message)}
    if set(retained) != set(originals) - set(aliases):
        raise ValueError("Repair would change entries other than verified blank album parts.")
    if any(key not in retained or key in conflicts for key in keeps):
        raise ValueError("Keep override does not identify an unambiguous retained entry.")

    corrected, carried = [], []
    for original in comparison["items"]:
        ident = original["id"]
        if ident not in retained:
            continue
        item = retained[ident]
        if any(getattr(item, key) != original[key] for key in ("text", "context", "links")):
            raise ValueError("A retained caption, context or link changed.")
        row, choice = deepcopy(original), deepcopy(choices[ident])
        row["grouped_id"] = item.grouped_id
        if ident in conflicts:
            details = "; ".join(f"Telegram {r['id'].split(':')[1]}: {r['decision'].title()}"
                                for r in conflicts[ident])
            row["review_warning"] = (f"This album received different choices: {details}. "
                                     "Decide whether the story belongs; extra attachments are already merged.")
            choice.update(final_decision=None, review_status="later", duplicate_of=None)
        elif ident in keeps:
            choice.update(final_decision="keep", review_status="decided", duplicate_of=None)
        if choice.get("duplicate_of") in aliases:
            choice["duplicate_of"] = aliases[choice["duplicate_of"]]["canonical_id"]
        corrected.append(row)
        carried.append(choice)

    counts = Counter(row["final_decision"] or "later" for row in carried)
    stats = {"original_entries": len(originals), "corrected_entries": len(corrected),
             "removed_blank_album_parts": len(aliases), "verified_albums": len(groups),
             "carried_final_decisions": sum(row["review_status"] == "decided" for row in carried),
             "conflicting_albums": conflicts, "confirmed_keep_overrides": list(keeps),
             "counts": {key: counts[key] for key in ("keep", "drop", "duplicate", "later")},
             "original_comparison_sha256": digest(comparison_raw),
             "original_review_sha256": digest(review_raw), "new_model_run": False}
    result = deepcopy(comparison)
    result["items"] = corrected
    result["album_repair"] = stats
    result["notice"] = (f"Album correction: {len(aliases)} blank attachment entries removed; "
                        f"{stats['carried_final_decisions']} decisions carried over. "
                        f"Only {len(conflicts)} albums have conflicting choices to resolve. "
                        "Luna recommendations are unchanged from the original audit.")
    result["summary"].update(
        items_reviewed=len(corrected), parent_posts=len(messages) - len(aliases),
        original_parent_message_parts=len(messages), textual_items=len(corrected),
        media_only_items_unassessed=0, original_counts=dict(Counter(r["original_status"] for r in corrected)),
        recommended_counts=dict(Counter(r["recommended_status"] for r in corrected)),
        differs_yes=sum(r["differs"] == "Yes" for r in corrected),
        differs_no=sum(r["differs"] == "No" for r in corrected),
        album_correction_only=True, new_model_run=False)
    result_raw = encoded(result)
    backup = {**review, "dataset_id": output.name, "dataset_sha256": digest(result_raw),
              "items": carried, "complete": not conflicts, "reviewed_count": len(carried) - len(conflicts),
              "remaining_count": len(conflicts), "counts": {**stats["counts"], "reviewed": len(carried) - len(conflicts)},
              "cursor_id": next(iter(conflicts), None), "album_repair": stats}
    contents = {"comparison.json": result_raw, "human-review.json": encoded(backup),
                "album-aliases.json": encoded(aliases), "repair-summary.json": encoded(stats),
                "repaired-messages.jsonl": "".join(json.dumps(m.record(), ensure_ascii=False) + "\n"
                                                  for m in messages).encode()}
    if any((output / name).exists() for name in [*contents, "manual-review.html"]):
        raise ValueError("Repair output already exists; it will not be overwritten.")
    output.mkdir(parents=True, exist_ok=True)
    for name, raw in contents.items():
        with (output / name).open("xb") as file:
            file.write(raw)
    build(output / "comparison.json", review=output / "human-review.json")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("comparison", "parents", "review", "metadata", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--keep", action="append", default=[], help="Explicit user-approved Keep override")
    args = parser.parse_args()
    print(json.dumps(repair(args.comparison, args.parents, args.review, args.metadata,
                            args.output, tuple(args.keep)), ensure_ascii=False, indent=2))
