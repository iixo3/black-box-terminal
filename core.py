"""Transparent relevance rules; no API clients, models, or factual validation."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit

UTC = timezone.utc
URL = re.compile(r"https?://[^\s<>]+", re.I)
BULLET = re.compile(r"^\s*[→•▪]\s*")


def safe_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False


@dataclass
class Link:
    start: int
    end: int
    url: str


@dataclass
class Message:
    source: str
    message_id: int
    date: datetime
    text: str
    links: list[Link] = field(default_factory=list)
    media_present: bool = False
    protected: bool = False
    grouped_id: int | None = None

    @property
    def source_url(self) -> str:
        return f"https://t.me/{self.source}/{self.message_id}"

    def record(self) -> dict:
        result = asdict(self)
        result["date"] = self.date.astimezone(UTC).isoformat()
        return result

    @classmethod
    def from_record(cls, value: dict) -> Message:
        date = datetime.fromisoformat(value["date"].replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("Message dates must include a timezone.")
        source = value["source"]
        if not re.fullmatch(r"[A-Za-z0-9_]+", source):
            raise ValueError("Invalid channel username.")
        grouped_id = value.get("grouped_id")
        if grouped_id is not None and type(grouped_id) is not int:
            raise ValueError("Album ID must be an integer or null.")
        return cls(source, int(value["message_id"]), date.astimezone(UTC), value.get("text", ""),
                   [Link(**link) for link in value.get("links", [])],
                   bool(value.get("media_present")), bool(value.get("protected")), grouped_id)


@dataclass
class Item:
    source: str
    message_id: int
    date: datetime
    index: int
    text: str
    links: list[str]
    context: str = ""
    media_present: bool = False
    protected: bool = False
    grouped_id: int | None = None

    @property
    def key(self) -> str:
        return f"{self.source.lower()}:{self.message_id}:{self.index}"

    @property
    def source_url(self) -> str:
        return f"https://t.me/{self.source}/{self.message_id}"


def split_message(message: Message) -> list[Item]:
    """Split roundup bullets and keep wrapped continuation lines and their links."""
    # Telegram exposes each album attachment as a separate message. Only members
    # with captions are news; empty members stay in the raw archive. This also
    # works when members arrive in different polls, without guessing neighbours.
    if message.grouped_id is not None and not message.text.strip():
        return []
    lines = []
    offset = 0
    for line in message.text.splitlines(keepends=True):
        lines.append((offset, offset + len(line), line))
        offset += len(line)
    roundup = (message.source.lower() == "defillama_tg" or
               bool(re.search(r"news round[ -]?up", message.text, re.I)))
    roundup = roundup and sum(bool(BULLET.match(line)) for _, _, line in lines) >= 2
    ranges: list[tuple[int, int, str]] = []
    if roundup:
        context = ""
        current: tuple[int, int, str] | None = None
        for start, end, line in lines:
            if BULLET.match(line):
                if current:
                    ranges.append(current)
                current = (start, end, context)
            elif line.strip() and (line.startswith((" ", "\t")) or URL.match(line.strip())):
                if current:
                    current = (current[0], end, current[2])
            else:
                if current:
                    ranges.append(current)
                    current = None
                if line.strip():
                    context = line.strip()
        if current:
            ranges.append(current)
    else:
        ranges = [(0, len(message.text), "")]
    items = []
    for index, (start, end, context) in enumerate(ranges):
        text = BULLET.sub("", message.text[start:end].strip()) if roundup else message.text
        links = [link.url for link in message.links
                 if link.start < end and link.end > start and safe_url(link.url)]
        links.extend(match.group(0).rstrip(".,;)") for match in URL.finditer(text))
        items.append(Item(message.source, message.message_id, message.date, index, text.strip(),
                          list(dict.fromkeys(links)), context, message.media_present, message.protected,
                          message.grouped_id))
    return items


@dataclass
class Decision:
    item: Item
    status: str
    reason: str
    matches: dict[str, list[str]] = field(default_factory=dict)
    duplicate_of: str | None = None

    def record(self) -> dict:
        result = asdict(self)
        result["item"]["date"] = self.item.date.astimezone(UTC).isoformat()
        return result


class Rules:
    def __init__(self, config: dict):
        self.config = config
        self.version = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
        self.crypto = [re.compile(pattern, re.I) for pattern in config["crypto_context"]]
        self.topics = {name: ([re.compile(pattern, re.I) for pattern in rule["patterns"]],
                              rule["requires_crypto"]) for name, rule in config["topics"].items()}
        self.exclusions = {name: [re.compile(pattern, re.I) for pattern in patterns]
                           for name, patterns in config["exclusions"].items()}
        self.material = re.compile(config["material_event_override"], re.I)

    @classmethod
    def load(cls, path: Path) -> Rules:
        return cls(json.loads(path.read_text()))

    def assess(self, item: Item) -> Decision:
        if not item.text.strip():
            reason = "media_only_no_text" if item.media_present else "empty_or_service_message"
            return Decision(item, "drop", reason)
        text = URL.sub("", item.text)
        context_hits = sorted({m.group(0) for pattern in self.crypto for m in pattern.finditer(text)})
        matches = {}
        for name, (patterns, requires_crypto) in self.topics.items():
            hits = sorted({m.group(0) for pattern in patterns for m in pattern.finditer(text)})
            if hits and (context_hits or not requires_crypto):
                matches[name] = hits
        for name, patterns in self.exclusions.items():
            hits = sorted({m.group(0) for pattern in patterns for m in pattern.finditer(text)})
            if hits and (name == "trading_signal_or_promotion" or not self.material.search(text)):
                return Decision(item, "drop", name, {"Excluded wording": hits, **matches})
        if not matches:
            reason = "no_topic_match" if context_hits else "no_crypto_or_direct_topic_context"
            return Decision(item, "drop", reason,
                            {"Crypto context": context_hits} if context_hits else {})
        if context_hits:
            matches["Crypto context"] = context_hits
        if item.protected:
            return Decision(item, "hold", "source_prohibits_forwarding", matches)
        return Decision(item, "keep", "topic_match", matches)


def normalized(text: str) -> str:
    text = URL.sub("", text.casefold())
    text = re.sub(r"^\W*(?:just in|breaking|update)\W*", "", text)
    text = re.sub(r"@(?:zoomerfiednews|watcherguru|aggrnewswire|treenewsfeed|defillama_tg)\b", "", text)
    for symbol, currency in [("$", "usd"), ("€", "eur"), ("£", "gbp")]:
        text = re.sub(re.escape(symbol) + r"(?=\s*\d)", currency + " ", text)
    # Keep numbers and asset/entity words: two banks or amounts must not collapse.
    return " ".join(re.findall(r"\w+(?:\.\d+)?", text))


class Deduplicator:
    def __init__(self, days: int = 7):
        self.window = timedelta(days=days)
        self.seen: list[tuple[datetime, str, str]] = []

    def seed(self, date: datetime, text: str, key: str) -> None:
        self.seen.append((date, normalized(text), key))

    def apply(self, decision: Decision) -> Decision:
        if decision.status != "keep":
            return decision
        item = decision.item
        self.seen = [entry for entry in self.seen if abs(item.date - entry[0]) <= self.window]
        text = normalized(item.text)
        articles = {"a", "an", "the"}
        content_words = [word for word in text.split() if word not in articles]
        for date, prior, key in self.seen:
            # Preserve word order and currencies: reversal of buyer/seller or amounts is news.
            same_content = content_words == [word for word in prior.split() if word not in articles]
            if text == prior or (len(text) >= 60 and same_content
                                 and SequenceMatcher(None, text, prior).ratio() >= .94):
                return Decision(item, "duplicate", "same_or_near_identical_headline",
                                decision.matches, key)
        self.seed(item.date, item.text, item.key)
        return decision


def evaluate(messages: list[Message], rules: Rules, dedup: Deduplicator | None = None) -> list[Decision]:
    dedup = dedup or Deduplicator(rules.config["duplicate_window_days"])
    items = [item for message in messages for item in split_message(message)]
    items.sort(key=lambda item: (item.date, item.source.lower(), item.message_id, item.index))
    return [dedup.apply(rules.assess(item)) for item in items]


def utf16_size(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def delivery_parts(item: Item) -> list[str]:
    body = item.text
    if item.context:
        body = f"{item.context}\n\n{body}"
    missing_links = [link for link in item.links if link not in body]
    if missing_links:
        body += "\n\nLinks:\n" + "\n".join(missing_links)
    body += f"\n\nSource: @{item.source}\n{item.source_url}"
    # Leave room for part labels; never truncate original text or hidden URLs.
    chunks, chunk, size = [], [], 0
    for char in body:
        width = 2 if ord(char) > 0xFFFF else 1
        if size + width > 3900:
            chunks.append("".join(chunk))
            chunk, size = [], 0
        chunk.append(char)
        size += width
    if chunk:
        chunks.append("".join(chunk))
    return [f"[{i + 1}/{len(chunks)}]\n{chunk}" if len(chunks) > 1 else chunk
            for i, chunk in enumerate(chunks)]
