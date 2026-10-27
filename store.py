"""SQLite archive, live checkpoints, and a durable delivery outbox."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core import Decision, Deduplicator, Message, delivery_parts


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, mode TEXT NOT NULL, rules_version TEXT NOT NULL,
          created TEXT NOT NULL, metadata TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (
          source TEXT NOT NULL, message_id INTEGER NOT NULL, date TEXT NOT NULL,
          payload TEXT NOT NULL, PRIMARY KEY(source, message_id));
        CREATE TABLE IF NOT EXISTS run_messages (
          run_id TEXT NOT NULL REFERENCES runs(id), source TEXT NOT NULL,
          message_id INTEGER NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY(run_id, source, message_id));
        CREATE TABLE IF NOT EXISTS decisions (
          run_id TEXT NOT NULL REFERENCES runs(id), item_key TEXT NOT NULL,
          source TEXT NOT NULL, date TEXT NOT NULL, status TEXT NOT NULL,
          reason TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id, item_key));
        CREATE INDEX IF NOT EXISTS decisions_date ON decisions(date);
        CREATE TABLE IF NOT EXISTS checkpoints (
          source TEXT PRIMARY KEY, message_id INTEGER NOT NULL, initialized TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS coverage (
          run_id TEXT NOT NULL REFERENCES runs(id), source TEXT NOT NULL,
          payload TEXT NOT NULL, PRIMARY KEY(run_id, source));
        CREATE TABLE IF NOT EXISTS outbox (
          item_key TEXT NOT NULL, part INTEGER NOT NULL, body TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', retry_at REAL NOT NULL DEFAULT 0,
          telegram_message_id INTEGER, error TEXT, PRIMARY KEY(item_key, part));
        """)
        os.chmod(path, 0o600)

    def close(self) -> None:
        self.db.close()

    def new_run(self, mode: str, rules_version: str, metadata: dict) -> str:
        run_id = f"{mode}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        with self.db:
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?)",
                            (run_id, mode, rules_version, datetime.now(timezone.utc).isoformat(),
                             json.dumps(metadata)))
        return run_id

    def checkpoint(self, source: str) -> int | None:
        row = self.db.execute("SELECT message_id FROM checkpoints WHERE source=?",
                              (source.lower(),)).fetchone()
        return row[0] if row else None

    def deduplicator(self, days: int, since: datetime) -> Deduplicator:
        result = Deduplicator(days)
        rows = self.db.execute("""SELECT d.payload FROM decisions d JOIN runs r ON r.id=d.run_id
          WHERE r.mode='live' AND d.status='keep' AND d.date>=? ORDER BY d.date""",
                               (since.isoformat(),)).fetchall()
        for row in rows:
            payload = json.loads(row[0])
            item = payload["item"]
            key = f"{item['source'].lower()}:{item['message_id']}:{item['index']}"
            result.seed(datetime.fromisoformat(item["date"]), item["text"], key)
        return result

    def save(self, run_id: str, messages: list[Message], decisions: list[Decision],
             coverage: dict[str, dict], checkpoints: dict[str, int] | None = None) -> None:
        """Archive + outbox + checkpoints commit together; replay never calls with checkpoints."""
        with self.db:
            for message in messages:
                self.db.execute("INSERT OR REPLACE INTO run_messages VALUES (?,?,?,?)",
                                (run_id, message.source.lower(), message.message_id,
                                 json.dumps(message.record(), ensure_ascii=False)))
                self.db.execute("INSERT OR REPLACE INTO messages VALUES (?,?,?,?)",
                                (message.source.lower(), message.message_id, message.date.isoformat(),
                                 json.dumps(message.record(), ensure_ascii=False)))
            for decision in decisions:
                item = decision.item
                self.db.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?)",
                                (run_id, item.key, item.source, item.date.isoformat(), decision.status,
                                 decision.reason, json.dumps(decision.record(), ensure_ascii=False)))
                if checkpoints is not None and decision.status == "keep":
                    for part, body in enumerate(delivery_parts(item)):
                        self.db.execute("INSERT OR IGNORE INTO outbox(item_key,part,body) VALUES (?,?,?)",
                                        (item.key, part, body))
            for source, info in coverage.items():
                self.db.execute("INSERT OR REPLACE INTO coverage VALUES (?,?,?)",
                                (run_id, source, json.dumps(info)))
            for source, message_id in (checkpoints or {}).items():
                self.db.execute("""INSERT INTO checkpoints VALUES (?,?,?)
                  ON CONFLICT(source) DO UPDATE SET message_id=excluded.message_id""",
                                (source.lower(), message_id, datetime.now(timezone.utc).isoformat()))

    def report_data(self, run_id: str) -> dict:
        run = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise ValueError("Unknown report run.")
        decisions = [json.loads(row[0]) for row in self.db.execute(
            "SELECT payload FROM decisions WHERE run_id=? ORDER BY date,item_key", (run_id,))]
        coverage = {row[0]: json.loads(row[1]) for row in self.db.execute(
            "SELECT source,payload FROM coverage WHERE run_id=?", (run_id,))}
        messages = [json.loads(row[0]) for row in self.db.execute(
            "SELECT payload FROM run_messages WHERE run_id=? ORDER BY source,message_id", (run_id,))]
        return {"run_id": run_id, "mode": run["mode"], "rules_version": run["rules_version"],
                "metadata": json.loads(run["metadata"]), "coverage": coverage,
                "messages": messages, "decisions": decisions}

    def recover_sending(self) -> int:
        with self.db:
            cursor = self.db.execute("""UPDATE outbox SET status='uncertain',
              error='Process stopped before delivery confirmation' WHERE status='sending'""")
        return cursor.rowcount

    def next_delivery(self):
        # An uncertain/failed earlier part blocks later parts of the same story.
        return self.db.execute("""SELECT o.* FROM outbox o WHERE o.status='pending' AND o.retry_at<=?
          AND NOT EXISTS (SELECT 1 FROM outbox p WHERE p.item_key=o.item_key AND p.part<o.part
            AND p.status!='sent') ORDER BY o.rowid LIMIT 1""", (time.time(),)).fetchone()

    def delivery_status(self, item_key: str, part: int, status: str, error: str | None = None,
                        telegram_id: int | None = None, retry_at: float = 0) -> None:
        with self.db:
            self.db.execute("""UPDATE outbox SET status=?,error=?,telegram_message_id=?,retry_at=?
              WHERE item_key=? AND part=?""", (status, error, telegram_id, retry_at, item_key, part))

    def status(self) -> dict:
        return {
            "checkpoints": {row[0]: row[1] for row in self.db.execute("SELECT source,message_id FROM checkpoints")},
            "delivery_counts": {row[0]: row[1] for row in self.db.execute("SELECT status,count(*) FROM outbox GROUP BY status")},
            "archived_messages": self.db.execute("SELECT count(*) FROM messages").fetchone()[0],
            "recent_runs": [dict(row) for row in self.db.execute(
                "SELECT id,mode,created FROM runs ORDER BY created DESC LIMIT 10")],
        }

    def retry(self, item_key: str) -> int:
        with self.db:
            cursor = self.db.execute("""UPDATE outbox SET status='pending',error=NULL,retry_at=0
              WHERE item_key=? AND status IN ('failed','uncertain')""", (item_key,))
        return cursor.rowcount
