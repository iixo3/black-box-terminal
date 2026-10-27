"""Telegram-only I/O. Reader requests only configured channel histories."""
from __future__ import annotations

import asyncio
import getpass
import json
import os
import re
import socket
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core import Link, Message, UTC, URL, safe_url


def load_credentials(path: Path, *, reader: bool = True, sender: bool = False) -> dict:
    if not path.is_file():
        raise ValueError("Local credentials are missing. Run Setup.command first.")
    if path.stat().st_mode & 0o077:
        raise ValueError("Credential file must be private: chmod 600 .local/credentials.json")
    value = json.loads(path.read_text())
    if reader and (not isinstance(value.get("api_id"), int) or not value.get("api_hash")):
        raise ValueError("Telegram API credentials are missing. Run Setup.command.")
    if sender and (not value.get("bot_token") or not value.get("destination")):
        raise ValueError("Bot token or private destination is missing. Run Setup.command.")
    return value


def new_client(credentials: dict, session: Path):
    from telethon import TelegramClient
    session.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = TelegramClient(str(session), credentials["api_id"], credentials["api_hash"],
                            receive_updates=False, flood_sleep_threshold=60,
                            device_model="Local crypto news watcher")
    client.session.save_entities = False
    return client


async def login(credentials: dict, session: Path) -> None:
    client = new_client(credentials, session)
    try:
        await client.start(phone=lambda: input("Telegram phone number (with country code): ").strip(),
                           code_callback=lambda: getpass.getpass("Telegram login code (hidden): ").strip(),
                           password=lambda: getpass.getpass("Telegram 2FA password (hidden): "))
        print("Telegram reader login saved locally.")
    finally:
        await client.disconnect()
        filename = Path(str(session) + ".session")
        if filename.exists():
            os.chmod(filename, 0o600)


def python_offset(text: str, units: int) -> int:
    """Telegram entity offsets use UTF-16 code units, not Python character offsets."""
    return len(text.encode("utf-16-le")[:units * 2].decode("utf-16-le", errors="ignore"))


def convert(source: str, message, channel_protected: bool = False) -> Message:
    text = message.message or ""
    links = []
    for entity in message.entities or []:
        kind = type(entity).__name__
        if kind not in {"MessageEntityTextUrl", "MessageEntityUrl"}:
            continue
        start = python_offset(text, entity.offset)
        end = python_offset(text, entity.offset + entity.length)
        url = entity.url if kind == "MessageEntityTextUrl" else text[start:end]
        if safe_url(url):
            links.append(Link(start, end, url))
    for match in URL.finditer(text):
        url = match.group(0).rstrip(".,;)")
        if not any(link.start == match.start() for link in links):
            links.append(Link(match.start(), match.start() + len(url), url))
    return Message(source, message.id, message.date.astimezone(UTC), text, links,
                   bool(message.media), channel_protected or bool(getattr(message, "noforwards", False)),
                   getattr(message, "grouped_id", None))


class Reader:
    def __init__(self, credentials: dict, session: Path, sources: list[str]):
        self.sources = sources
        self.client = new_client(credentials, session)

    async def __aenter__(self):
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            raise ValueError("Reader is not logged in. Run Setup.command locally.")
        return self

    async def __aexit__(self, *args):
        await self.client.disconnect()

    async def entity(self, source: str):
        if source not in self.sources:
            raise ValueError("Source is not in the configured allowlist.")
        from telethon.tl.types import Channel
        entity = await self.client.get_entity(source)
        if not isinstance(entity, Channel) or not entity.broadcast:
            raise ValueError("Configured source must be a broadcast channel.")
        return entity

    async def latest_id(self, source: str) -> int:
        entity = await self.entity(source)
        messages = await self.client.get_messages(entity, limit=1)
        return messages[0].id if messages else 0

    async def collect_live(self, checkpoints: dict[str, int | None]):
        messages, coverage, updates = [], {}, {}
        for source in self.sources:
            try:
                entity = await self.entity(source)
                cursor = checkpoints[source]
                if cursor is None:
                    latest = await self.client.get_messages(entity, limit=1)
                    updates[source] = latest[0].id if latest else 0
                    coverage[source] = {"status": "initialized", "messages": 0,
                                        "note": "Starts after current latest post; no historical relay."}
                    continue
                fetched = []
                async for message in self.client.iter_messages(entity, min_id=cursor,
                                                                reverse=True, limit=None, wait_time=1):
                    fetched.append(convert(source, message, bool(getattr(entity, "noforwards", False))))
                messages.extend(fetched)
                updates[source] = max([cursor, *(message.message_id for message in fetched)])
                coverage[source] = {"status": "ok", "messages": len(fetched),
                                    "note": f"After message {cursor}; complete readable new history."}
            except Exception as error:
                # Keep the old checkpoint. Only exception class is recorded; no secrets in logs.
                coverage[source] = {"status": "error", "messages": 0,
                                    "note": f"{type(error).__name__}; checkpoint retained; retry next poll."}
        return messages, coverage, updates

    async def collect_range(self, start: datetime, end: datetime):
        messages, coverage = [], {}
        for source in self.sources:
            fetched = []
            print(f"Reading historical channel: @{source}", flush=True)
            try:
                entity = await self.entity(source)
                # Fetch everything in the period, including irrelevant/empty posts. No server search.
                async for message in self.client.iter_messages(entity, offset_date=end,
                                                                limit=None, wait_time=1):
                    if message.date < start:
                        break
                    if start <= message.date < end:
                        fetched.append(convert(source, message, bool(getattr(entity, "noforwards", False))))
                        if len(fetched) % 500 == 0:
                            print(f"  @{source}: {len(fetched)} messages read", flush=True)
                coverage[source] = {"status": "ok" if fetched else "empty", "messages": len(fetched),
                                    "note": "Readable history scanned for full requested interval; deleted posts unavailable."}
            except Exception as error:
                coverage[source] = {"status": "partial" if fetched else "error", "messages": len(fetched),
                                    "note": f"{type(error).__name__}; requested history is incomplete."}
            messages.extend(fetched)
            print(f"  @{source}: {coverage[source]['status']} ({len(fetched)} messages)", flush=True)
        return messages, coverage

    async def collect_preview(self, limit: int):
        messages, coverage = [], {}
        for source in self.sources:
            fetched = []
            try:
                entity = await self.entity(source)
                async for message in self.client.iter_messages(entity, limit=limit, wait_time=1):
                    fetched.append(convert(source, message, bool(getattr(entity, "noforwards", False))))
                coverage[source] = {"status": "sample", "messages": len(fetched),
                                    "note": f"Up to {limit} latest messages; sample only, not full historical coverage."}
            except Exception as error:
                coverage[source] = {"status": "partial" if fetched else "error", "messages": len(fetched),
                                    "note": f"{type(error).__name__}; preview incomplete."}
            messages.extend(fetched)
        return messages, coverage


class BotError(Exception):
    def __init__(self, code: int, retry_after: int = 0):
        self.code, self.retry_after = code, retry_after
        super().__init__(f"Telegram Bot API error {code}")


class UncertainDelivery(Exception):
    pass


class BotConnectionError(Exception):
    """Secret-safe connection diagnostic for requests that do not send messages."""


def bot_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # python.org installations on macOS may have no default CA bundle installed.
    if sys.platform == "darwin" and not context.get_ca_certs():
        bundle = Path("/etc/ssl/cert.pem")
        if bundle.is_file():
            context.load_verify_locations(cafile=str(bundle))
    return context


def bot_connection_failure(method: str, error: Exception) -> Exception:
    if method not in {"getMe", "getChat", "getChatMember", "getUpdates"}:
        # A send may have reached Telegram even when its response was lost.
        return UncertainDelivery("Network result unknown; inspect destination before retrying.")
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        message = "Bot API TLS certificate verification failed. Python cannot verify Telegram's HTTPS certificate."
    elif isinstance(reason, ssl.SSLError):
        message = "Secure connection to Telegram Bot API failed. Retry setup."
    elif isinstance(reason, TimeoutError):
        message = "Telegram Bot API connection timed out. Retry setup."
    elif isinstance(reason, socket.gaierror):
        message = "Telegram Bot API hostname could not be resolved. Check your internet connection."
    elif isinstance(reason, ValueError):
        message = "Telegram Bot API returned an unreadable response. Retry setup."
    else:
        message = "Could not connect to Telegram Bot API. Check your internet connection and retry setup."
    return BotConnectionError(message)


class Bot:
    def __init__(self, credentials: dict):
        token = credentials["bot_token"]
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise ValueError("Bot token format is invalid; update it through Setup.command.")
        self.token = token
        self.destination = str(credentials["destination"])

    def request(self, method: str, payload: dict) -> dict:
        request = Request(f"https://api.telegram.org/bot{self.token}/{method}",
                          json.dumps(payload).encode(), {"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30, context=bot_ssl_context()) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                result = json.load(error)
            except (ValueError, OSError) as response_error:
                raise bot_connection_failure(method, response_error) from None
        except (URLError, TimeoutError, OSError, ValueError) as error:
            # Never include exception contents: they may embed the bot token in a URL.
            raise bot_connection_failure(method, error) from None
        if not result.get("ok"):
            raise BotError(int(result.get("error_code", 500)), int(result.get("parameters", {}).get("retry_after", 0)))
        return result["result"]

    def preflight(self) -> None:
        me = self.request("getMe", {})
        chat = self.request("getChat", {"chat_id": self.destination})
        if chat.get("type") != "channel" or chat.get("username"):
            raise ValueError("Destination must be a private Telegram channel (no public username).")
        member = self.request("getChatMember", {"chat_id": self.destination, "user_id": me["id"]})
        if member.get("status") not in {"administrator", "creator"} or not member.get("can_post_messages", member.get("status") == "creator"):
            raise ValueError("The bot needs channel administrator permission to post messages.")

    def send(self, body: str) -> int:
        return int(self.request("sendMessage", {"chat_id": self.destination, "text": body,
                                               "link_preview_options": {"is_disabled": True},
                                               "disable_notification": True})["message_id"])


async def deliver(store, bot: Bot, limit: int) -> dict:
    result = {"sent": 0, "uncertain": 0, "failed": 0}
    for _ in range(limit):
        row = store.next_delivery()
        if row is None:
            break
        key, part = row["item_key"], row["part"]
        store.delivery_status(key, part, "sending")
        try:
            message_id = await asyncio.to_thread(bot.send, row["body"])
        except BotError as error:
            retryable = error.code == 429 or error.code >= 500
            store.delivery_status(key, part, "pending" if retryable else "failed", str(error),
                                  retry_at=time.time() + max(error.retry_after, 60))
            if not retryable:
                result["failed"] += 1
            break
        except UncertainDelivery as error:
            store.delivery_status(key, part, "uncertain", str(error))
            result["uncertain"] += 1
            break
        else:
            store.delivery_status(key, part, "sent", telegram_id=message_id)
            result["sent"] += 1
        await asyncio.sleep(1.1)
    return result
