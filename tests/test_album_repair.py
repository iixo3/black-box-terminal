from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from repair_album_review import repair


class AlbumRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "corrected"
        self.paths = {name: self.root / (name + ".json")
                      for name in ("comparison", "parents", "review", "metadata")}
        parents, items, choices, records = [], [], [], []
        for ident, text, choice in [(1, "Bank launches stablecoin payments", "keep"),
                                    (2, "", "drop"), (3, "", "drop"),
                                    (4, "New crypto custody product", "drop")]:
            row = {"id": f"watcherguru:{ident}:0", "source": "WatcherGuru",
                   "date": "2026-09-01T12:00:00+00:00", "text": text, "context": "",
                   "links": ["https://example.com/source"] if text else [],
                   "source_url": f"https://t.me/WatcherGuru/{ident}",
                   "original_status": "keep" if text else "drop",
                   "recommended_status": "keep" if text else "drop", "differs": "No"}
            items.append(row)
            choices.append({**row, "final_decision": choice, "review_status": "decided", "note": "",
                            "duplicate_of": None, "decided_at": "2026-10-19T12:00:00Z"})
            parents.append({"source": "WatcherGuru", "message_id": ident, "date": row["date"],
                            "text": text, "links": [], "media_present": ident < 4, "protected": False})
            # Link values must match across the raw archive and comparison.
            parents[-1]["links"] = [{"start": 0, "end": len(text), "url": url} for url in row["links"]]
            if ident < 4:
                records.append({"source": "WatcherGuru", "message_id": ident, "grouped_id": 123,
                                "text_sha256": hashlib.sha256(text.encode()).hexdigest()})
        comparison = {"summary": {}, "items": items, "duplicate_groups": []}
        self.paths["comparison"].write_text(json.dumps(comparison))
        self.paths["parents"].write_text("".join(json.dumps(row) + "\n" for row in parents))
        review = {"schema_version": 1, "complete": True, "title": "September 2026",
                  "dataset_sha256": hashlib.sha256(self.paths["comparison"].read_bytes()).hexdigest(),
                  "items": choices, "exported_at": "2026-10-19T12:00:00Z"}
        self.paths["review"].write_text(json.dumps(review))
        self.paths["metadata"].write_text(json.dumps({"records": records}))

    def run_repair(self):
        return repair(*(self.paths[name] for name in ("comparison", "parents", "review", "metadata")),
                      self.output, ("watcherguru:4:0",))

    def test_multiple_blank_parts_merge_and_conflicting_choices_remain_pending(self):
        original = {name: path.read_bytes() for name, path in self.paths.items()}
        stats = self.run_repair()
        self.assertEqual(stats["removed_blank_album_parts"], 2)
        self.assertEqual(stats["corrected_entries"], 2)
        backup = json.loads((self.output / "human-review.json").read_text())
        self.assertEqual([(r["id"], r["final_decision"]) for r in backup["items"]],
                         [("watcherguru:1:0", None), ("watcherguru:4:0", "keep")])
        self.assertEqual(backup["remaining_count"], 1)
        repaired = json.loads((self.output / "comparison.json").read_text())
        self.assertEqual(repaired["items"][0]["links"], ["https://example.com/source"])
        self.assertIn("different choices", repaired["items"][0]["review_warning"])
        page = (self.output / "manual-review.html").read_text()
        self.assertIn('"initial_state"', page)
        self.assertNotIn("__REVIEW_DATA__", page)
        self.assertEqual(len((self.output / "repaired-messages.jsonl").read_text().splitlines()), 4)
        self.assertTrue(all(path.read_bytes() == original[name] for name, path in self.paths.items()))
        page_before = (self.output / "manual-review.html").read_bytes()
        with self.assertRaisesRegex(ValueError, "not be overwritten"):
            self.run_repair()
        self.assertEqual((self.output / "manual-review.html").read_bytes(), page_before)

    def test_metadata_for_changed_caption_is_rejected_before_output(self):
        metadata = json.loads(self.paths["metadata"].read_text())
        metadata["records"][0]["text_sha256"] = "incorrect"
        self.paths["metadata"].write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "does not match archived content"):
            self.run_repair()
        self.assertFalse(self.output.exists())
