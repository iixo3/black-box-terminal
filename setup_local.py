#!/usr/bin/env python3
"""Interactive local setup: enter credentials here, never into the chat."""
from __future__ import annotations

import asyncio
import getpass
import json
import os
import re
import sys

from telegram_io import Bot, BotConnectionError, BotError, login
from watcher import CREDS, LOCAL, SESSION, process_lock


def save(value: dict) -> None:
    LOCAL.mkdir(exist_ok=True, mode=0o700)
    temp = CREDS.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2))
    os.chmod(temp, 0o600)
    temp.replace(CREDS)


def main() -> None:
    os.umask(0o077)
    value = json.loads(CREDS.read_text()) if CREDS.is_file() else {}
    print("LOCAL SETUP — values stay on this Mac. Do not paste them into the chat.")
    print("1. Open https://my.telegram.org → API development tools → create a personal application.")
    print("   Suggested name: Crypto News Watcher; platform: Desktop. Obtain api_id and api_hash.")
    api_id = input("api_id (Enter keeps saved value): ").strip()
    if api_id:
        if not api_id.isdecimal() or int(api_id) < 1:
            raise ValueError("api_id must be a positive integer.")
        value["api_id"] = int(api_id)
    api_hash = getpass.getpass("api_hash (hidden; Enter keeps saved value): ").strip()
    if api_hash:
        if not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
            raise ValueError("api_hash should contain 32 hexadecimal characters.")
        value["api_hash"] = api_hash
    if not value.get("api_id") or not value.get("api_hash"):
        raise ValueError("Both Telegram API values are required for reader setup.")
    save(value)
    with process_lock():
        asyncio.run(login(value, SESSION))
    print("\n2. In Telegram, open verified @BotFather, use /newbot, and obtain its token.")
    print("   Create a PRIVATE channel and add this bot as administrator with permission to post.")
    token = getpass.getpass("Bot token (hidden; Enter keeps saved token or defers bot setup): ").strip()
    if token:
        value["bot_token"] = token
    if not value.get("bot_token"):
        print("Reader ready. Bot setup deferred. September replay can run without a bot.")
        return
    value.setdefault("destination", "")
    bot = Bot(value)
    me = bot.request("getMe", {})
    print(f"Bot confirmed: @{me['username']}. Add it to your PRIVATE channel as administrator.")
    print("Post 'watcher setup' yourself in that channel, then continue below.")
    destination = input("Numeric channel ID (-100…), or Enter to discover from that setup post: ").strip()
    if not destination:
        if value.get("destination"):
            destination = str(value["destination"])
        else:
            updates = bot.request("getUpdates", {"timeout": 0, "allowed_updates": ["channel_post"]})
            chats = {entry["channel_post"]["chat"]["id"]: entry["channel_post"]["chat"]
                     for entry in updates if "channel_post" in entry
                     and entry["channel_post"]["chat"].get("type") == "channel"
                     and not entry["channel_post"]["chat"].get("username")}
            if not chats:
                save(value)
                raise ValueError("No private setup post found. Add bot, post in the channel, then rerun Setup.command.")
            if len(chats) == 1:
                chat_id, chat = next(iter(chats.items()))
                print(f"Found private channel: {chat.get('title', '(untitled)')}")
                confirm = input("Use this destination? [y/N]: ").strip().lower()
                if confirm != "y":
                    save(value)
                    raise ValueError("Destination not selected. Rerun setup when ready.")
                destination = str(chat_id)
            else:
                for chat_id, chat in chats.items():
                    print(f"  {chat_id}: {chat.get('title', '(untitled)')}")
                destination = input("Choose numeric private channel ID: ").strip()
    if not re.fullmatch(r"-100\d+", destination):
        raise ValueError("Private channel ID should start with -100 and contain digits.")
    value["destination"] = destination
    bot = Bot(value)
    bot.preflight()
    save(value)
    print("\nReader and private destination configured. No news has been sent; background watcher is inactive.")
    print("Next: double-click Replay September.command to review September before enabling the live watcher.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Setup stopped; saved API credentials remain private.")
        sys.exit(130)
    except (BotConnectionError, BotError) as error:
        if isinstance(error, BotError) and error.code == 401:
            print("Setup failed: Telegram rejected the bot token (401). Copy its HTTP API token from @BotFather.",
                  file=sys.stderr)
        else:
            print(f"Setup failed: {error}", file=sys.stderr)
        sys.exit(1)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        print(f"Setup failed: {type(error).__name__}. No secret values were printed.", file=sys.stderr)
        sys.exit(1)
