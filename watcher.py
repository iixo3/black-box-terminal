#!/usr/bin/env python3
"""Local news relay CLI. Historical/offline replay can never send Telegram messages."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import plistlib
import signal
import subprocess
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core import Message, Rules, evaluate
from reports import write_report
from store import Store

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / ".local"
CREDS = LOCAL / "credentials.json"
SESSION = LOCAL / "reader"
LABEL = "com.local.crypto-news-watcher"


@contextmanager
def process_lock():
    LOCAL.mkdir(exist_ok=True, mode=0o700)
    with (LOCAL / "watcher.lock").open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another watcher/login/replay is running. Stop it before continuing.") from None
        yield


def date_range(start: str, end: str, timezone_name: str):
    zone = ZoneInfo(timezone_name)
    start_date = datetime.fromisoformat(start)
    end_date = datetime.fromisoformat(end)
    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=zone)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=zone)
    start_date, end_date = start_date.astimezone(timezone.utc), end_date.astimezone(timezone.utc)
    if end_date <= start_date:
        raise ValueError("Replay end must be after start; end is exclusive.")
    return start_date, end_date


def summarize(data: dict) -> None:
    counts = Counter(d["status"] for d in data["decisions"])
    print(f"Run: {data['run_id']} | " + " | ".join(f"{state}: {counts[state]}" for state in ["keep", "drop", "duplicate", "hold"]))
    for source, info in data["coverage"].items():
        print(f"  {source}: {info['status']} ({info['messages']} messages)")


def save_review(store: Store, messages: list[Message], coverage: dict, rules: Rules,
                mode: str, metadata: dict) -> tuple[Path, dict]:
    run_id = store.new_run(mode, rules.version, metadata)
    store.save(run_id, messages, evaluate(messages, rules), coverage)
    data = store.report_data(run_id)
    output = write_report(data, rules.config, ROOT / "reports")
    summarize(data)
    print(f"Review report: {output}")
    if mode in {"replay", "preview"}:
        (LOCAL / "last_review.json").write_text(json.dumps({"run_id": run_id, "rules_version": rules.version,
                                                          "coverage": coverage, "report": str(output)}))
    return output, data


async def historical(args, store: Store, rules: Rules) -> int:
    start, end = date_range(args.start, args.end, rules.config["timezone"])
    if args.input:
        records = [json.loads(line) for line in Path(args.input).read_text().splitlines() if line.strip()]
        all_messages = [Message.from_record(value) for value in records]
        if any(m.source not in rules.config["sources"] for m in all_messages):
            raise ValueError("Input contains sources outside configured channel allowlist.")
        messages = [m for m in all_messages if start <= m.date < end]
        coverage = {source: {"status": "offline_input", "messages": sum(m.source == source for m in messages),
                             "note": "Input file only; no claim of complete Telegram history."}
                    for source in rules.config["sources"]}
        mode = "fixture" if records and all(v.get("synthetic") for v in records) else "offline"
    else:
        from telegram_io import Reader, load_credentials
        credentials = load_credentials(CREDS)
        async with Reader(credentials, SESSION, rules.config["sources"]) as reader:
            messages, coverage = await reader.collect_range(start, end)
        mode = "replay"
    _, data = save_review(store, messages, coverage, rules, mode,
                         {"start_inclusive": start.isoformat(), "end_exclusive": end.isoformat(),
                          "timezone": rules.config["timezone"], "sends_messages": False})
    return 1 if any(info["status"] in {"error", "partial"} for info in data["coverage"].values()) else 0


async def preview(args, store: Store, rules: Rules) -> int:
    from telegram_io import Reader, load_credentials
    async with Reader(load_credentials(CREDS), SESSION, rules.config["sources"]) as reader:
        messages, coverage = await reader.collect_preview(args.limit)
    save_review(store, messages, coverage, rules, "preview", {"latest_per_source": args.limit, "sends_messages": False})
    return 1 if any(info["status"] in {"error", "partial"} for info in coverage.values()) else 0


async def watch(args, store: Store, rules: Rules) -> int:
    from telegram_io import Bot, Reader, deliver, load_credentials
    credentials = load_credentials(CREDS, sender=True)
    bot = Bot(credentials)
    await asyncio.to_thread(bot.preflight)
    recovered = store.recover_sending()
    if recovered:
        print(f"Paused {recovered} unconfirmed delivery parts; inspect destination before retrying.", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    while not stop.is_set():
        # Reload editable relevance rules each poll.
        rules = Rules.load(ROOT / "config.json")
        try:
            async with Reader(credentials, SESSION, rules.config["sources"]) as reader:
                messages, coverage, updates = await reader.collect_live(
                    {source: store.checkpoint(source) for source in rules.config["sources"]})
            since = (min((m.date for m in messages), default=datetime.now(timezone.utc))
                     - timedelta(days=rules.config["duplicate_window_days"]))
            decisions = evaluate(messages, rules, store.deduplicator(rules.config["duplicate_window_days"], since))
            run_id = store.new_run("live", rules.version, {"sends_messages": True})
            store.save(run_id, messages, decisions, coverage, checkpoints=updates)
            summarize(store.report_data(run_id))
            result = await deliver(store, bot, rules.config["max_deliveries_per_poll"])
            print(f"Delivery: {json.dumps(result)}", flush=True)
            if args.once:
                return 1 if any(info["status"] == "error" for info in coverage.values()) or result["failed"] or result["uncertain"] else 0
        except Exception as error:
            print(f"Poll failed: {type(error).__name__}. History/checkpoints retained; retry next poll.", flush=True)
            if args.once:
                raise
        try:
            await asyncio.wait_for(stop.wait(), timeout=rules.config["poll_seconds"])
        except asyncio.TimeoutError:
            pass
    return 0


def background_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def background(enable: bool, rules: Rules) -> None:
    path = background_path()
    domain = f"gui/{os.getuid()}"
    if not enable:
        result = subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
        print("Background watcher stopped." if result.returncode == 0 else "Background watcher was not loaded.")
        if path.exists():
            path.unlink()
        return
    from telegram_io import load_credentials
    load_credentials(CREDS, sender=True)
    session = Path(str(SESSION) + ".session")
    if not session.is_file():
        raise ValueError("Log into reader first with Setup.command.")
    review_path = LOCAL / "last_review.json"
    if not review_path.is_file() or not (LOCAL / "delivery_verified.json").is_file():
        raise ValueError("Run a Telegram preview/replay, inspect its report, then run test-delivery before enabling.")
    review = json.loads(review_path.read_text())
    if review["rules_version"] != rules.version:
        raise ValueError("Rules changed since review. Run preview/replay with current rules first.")
    if any(info["status"] in {"error", "partial"} for info in review["coverage"].values()):
        raise ValueError("Source review is incomplete. Resolve access errors before enabling.")
    credential_hash = __import__("hashlib").sha256(CREDS.read_bytes()).hexdigest()
    verified = json.loads((LOCAL / "delivery_verified.json").read_text())
    if verified["credentials_hash"] != credential_hash:
        raise ValueError("Destination credentials changed. Repeat test-delivery first.")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("Watcher LaunchAgent already exists. Disable it before re-enabling.")
    plist = {"Label": LABEL, "ProgramArguments": [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "watcher.py"), "watch"],
             "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 60,
             "StandardOutPath": str(LOCAL / "watcher.log"), "StandardErrorPath": str(LOCAL / "watcher-error.log"),
             "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"}, "Umask": 0o077}
    with path.open("wb") as file:
        plistlib.dump(plist, file)
    os.chmod(path, 0o600)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True)
    if result.returncode:
        path.unlink()
        raise ValueError(f"Could not enable LaunchAgent (launchctl exit {result.returncode}).")
    print("Background watcher enabled; launches at Mac login and checks every 15 minutes.")


def main(argv=None) -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replay_parser = commands.add_parser("replay", help="Historical review only; never sends or advances live checkpoints")
    replay_parser.add_argument("--start", required=True, help="Inclusive local date, e.g. 2026-09-01")
    replay_parser.add_argument("--end", required=True, help="Exclusive local date, e.g. 2026-10-01")
    replay_parser.add_argument("--input", help="Optional offline messages JSONL; no Telegram connection")
    preview_parser = commands.add_parser("preview", help="Review latest messages without sending")
    preview_parser.add_argument("--limit", type=int, default=25)
    watch_parser = commands.add_parser("watch", help="Start live collection and relay")
    watch_parser.add_argument("--once", action="store_true")
    commands.add_parser("test-delivery", help="Send one clearly labeled test into your private channel")
    commands.add_parser("doctor", help="Local setup checks only; no network or credentials printed")
    commands.add_parser("status")
    commands.add_parser("enable-background")
    commands.add_parser("disable-background")
    retry_parser = commands.add_parser("retry-delivery", help="Retry a failed/uncertain story after checking destination")
    retry_parser.add_argument("item_key")
    retry_parser.add_argument("--acknowledge-possible-duplicate", action="store_true")
    args = parser.parse_args(argv)
    rules = Rules.load(ROOT / "config.json")
    if len(set(s.lower() for s in rules.config["sources"])) != len(rules.config["sources"]):
        raise ValueError("Source allowlist contains duplicates.")
    if args.command in {"enable-background", "disable-background"}:
        background(args.command == "enable-background", rules)
        return 0
    if args.command == "doctor":
        import importlib.util
        checks = {"rules_valid": True, "telethon_installed": importlib.util.find_spec("telethon") is not None,
                  "credentials_present": CREDS.is_file(), "reader_session_present": Path(str(SESSION) + ".session").is_file(),
                  "background_installed": background_path().is_file()}
        print(json.dumps(checks, indent=2))
        return 0 if checks["telethon_installed"] and checks["credentials_present"] and checks["reader_session_present"] else 1
    if args.command == "test-delivery":
        from telegram_io import Bot, load_credentials
        bot = Bot(load_credentials(CREDS, reader=False, sender=True))
        bot.preflight()
        bot.send("SETUP TEST — Crypto news watcher\nPrivate delivery works. This is a test message, not news.")
        digest = __import__("hashlib").sha256(CREDS.read_bytes()).hexdigest()
        (LOCAL / "delivery_verified.json").write_text(json.dumps({"credentials_hash": digest}))
        print("Private channel delivery verified. Live watcher has not been started.")
        return 0
    with process_lock():
        database = "review.sqlite" if args.command in {"replay", "preview"} else "archive.sqlite"
        store = Store(LOCAL / database)
        try:
            if args.command == "status":
                print(json.dumps(store.status(), indent=2))
                return 0
            if args.command == "retry-delivery":
                if not args.acknowledge_possible_duplicate:
                    raise ValueError("Inspect destination first; add --acknowledge-possible-duplicate to retry.")
                print(f"Queued {store.retry(args.item_key)} delivery parts for next live poll.")
                return 0
            if args.command == "replay":
                return asyncio.run(historical(args, store, rules))
            if args.command == "preview":
                if args.limit < 1:
                    raise ValueError("Preview limit must be positive.")
                return asyncio.run(preview(args, store, rules))
            if args.command == "watch":
                return asyncio.run(watch(args, store, rules))
        finally:
            store.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Stopped.")
        sys.exit(130)
    except (ValueError, FileNotFoundError, ModuleNotFoundError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        # Some library/network exceptions embed credentials; never print their contents.
        print(f"Command failed: {type(error).__name__}. Local credentials were not printed.", file=sys.stderr)
        sys.exit(1)
