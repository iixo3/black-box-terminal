# Black Box Terminal

A news aggregator that separates noise from information worth keeping, centred on tokenization, onchain finance and the convergence of blockchain with capital markets. Built on a local Telegram news collector with transparent keep/drop rules.

**Status:** local prototype. Nothing runs online: the collector runs only on a single Mac, the Telegram relay has never been switched on, and nothing is published.

**Demo:** open `design-prototype/preview.html` in a browser to see the design of the public archive with 20 stories from September 2026. Nothing to install.

## Vision

Crypto news arrives through fast feeds full of price moves, promotions and repeated headlines. Black Box Terminal collects those feeds, drops the noise with transparent rules, and keeps the developments worth keeping: tokenization, onchain finance and the convergence of blockchain with capital markets.

What it keeps becomes a chronological, sourced record. Each entry is a short factual summary in our own words, linked to a source that supports it, so a question such as "what happened with tokenized funds in September?" takes a search instead of an afternoon of scrolling. It is a filter and an archive, not an exhaustive history and not a fact-checking service.

The project has two content scopes:

- **Private watcher:** a broad filter over crypto and blockchain product news (stablecoins, tokenization, DeFi, payments, regulation, infrastructure, funding, research and security).
- **Public archive, version 1:** tokenization, RWA and stablecoins only, plus directly related product, regulation and infrastructure news. Broadening the private rules never silently broadens the public archive.

## Thesis and what is being tested

- A sourced, chronological archive cuts the effort of finding and checking past developments.
- One person can maintain it with no additional running cost.
- Outside readers (product people, ecosystem users, journalists, analysts) find enough value to come back. No primary audience has been validated yet; approval of the design prototype tests design preference, not demand.

Success checks for a first version: a reader can find relevant records, open a source and come back without losing their place, and tell a proposal from a launch; the editor can review a daily batch without repeating full manual passes and produce useful sourced periodic digests.

## Key design choices

- **Relevance, duplicates and factual support are separate decisions.** A story can be relevant but a duplicate, or relevant but not yet backed by an original source. Relevance review is not fact-checking.
- **Stages matter.** Exploration, proposal, approval, pilot and launch are different records, not repeats of one story.
- **Conservative de-duplication.** Only identical or near-identical headlines within seven days are merged; different amounts, entities or wording stay separate.
- **Every decision is explained.** Reports record the rule and the exact words that matched, so omissions can be audited.
- **Roundups are read item by item.** Weekly DeFi roundups are kept whole, and each bullet is assessed with its own link.
- **Replays are safe.** Historical replays never send messages and never move the live checkpoints.
- **No silent re-sends.** Delivery uses a durable outbox; an uncertain send is held for a human decision rather than repeated.
- **Human approval before automatic publishing.** The planned pilot is seven days of daily batch approval before any automatic publication. It has not started.
- **Phone first.** Desktop shows category columns; phone shows one category at a time; both share one article reader.

## How it works

```
5 Telegram news channels
  -> local collector (Telethon, every 15 minutes while the Mac is awake)
  -> raw archive + keyword relevance and duplicate decisions (SQLite)
  -> durable outbox
  -> optional private Telegram bot relay (not enabled)
```

Sources: ZoomerfiedNews, WatcherGuru, AGGRNEWSWIRE, TreeNewsFeed and defillama_tg. Relevance rules, topics and exclusions are plain regular expressions in [config.json](config.json). No AI model or API key is used by the collector.

September test: a replay of September's posts produced 563 candidate entries, which an AI model (Luna 6.0) audited. After correcting how Telegram albums were split, a full human review settled all 490 remaining entries: 191 keep, 299 drop.

| File | What it does |
|---|---|
| `watcher.py` | Command line: replay, preview, watch, status, doctor, delivery and background controls |
| `core.py` | Message model, relevance rules, roundup splitting, duplicate detection |
| `telegram_io.py` | Telegram reader (user session, read-only, allowlisted channels) and bot sender |
| `store.py` | SQLite archive, checkpoints and delivery outbox |
| `reports.py` | HTML, CSV and JSON replay reports |
| `setup_local.py` | Local setup wizard for Telegram credentials |
| `manual_review.py`, `manual_review.html` | Offline keep/drop/duplicate review page |
| `repair_album_review.py` | Rebuilds the review set after the album correction |
| `design-prototype/` | Static design prototype of the public archive (see its [README](design-prototype/README.md)) |
| `examples/demo.jsonl` | Fictional sample posts for testing without Telegram |
| `tests/` | Unit tests |

## Run it

Requires macOS (the launchers are `.command` files), Python 3.10 or newer, and internet access for Telegram.

### Try it without Telegram

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python watcher.py replay --start 2026-09-01 --end 2026-10-01 --input examples/demo.jsonl
node --test design-prototype/scroll-controller.test.cjs
```

The demo posts are fictional. They test the pipeline and the report format, not real coverage.

### Design prototype

Open `design-prototype/preview.html` in a browser, or serve the folder from the project root:

```bash
python3 -m http.server 8767 --bind 127.0.0.1 --directory design-prototype
```

Then open `http://127.0.0.1:8767/preview.html`.

### Set up the collector

1. Double-click **Setup.command**. It creates a Python environment, installs Telethon and asks for credentials in the Terminal.
2. On [my.telegram.org](https://my.telegram.org), open **API development tools**, register a personal application and enter its `api_id` and `api_hash` when asked. Log in to Telegram in the same Terminal; codes and passwords are hidden as you type.
3. Optional relay: in Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot`, and enter the token when asked. Create a private channel, add the bot as an administrator that can post, and post `watcher setup` in it; the wizard finds the channel from that post.

Credentials, the Telegram session and the archives stay in `.local/`, which is git-ignored and readable only by your user. Never put them in a chat, a repository or a screenshot. A Telegram user session can act for the whole account: if it is ever exposed, end it in Telegram under **Settings → Devices**.

### Replay a month

Double-click **Replay September.command**, or:

```bash
.venv/bin/python watcher.py replay --start 2026-09-01 --end 2026-10-01
```

This reads every available post in the period and writes a searchable HTML report plus CSV, JSON, the raw posts and the exact rules used, into `reports/`. To tune the rules, edit `config.json` and replay the same captured posts without connecting again:

```bash
.venv/bin/python watcher.py replay --start 2026-09-01 --end 2026-10-01 --input reports/REPLACE_WITH_RUN.messages.jsonl
```

Dates use the time zone set in `config.json` (UTC by default); the end date is exclusive. Images and videos are flagged, but only their text or caption is assessed.

### Review by hand

**Review September.command** opens an offline review page: arrow keys for drop and keep, `D` duplicate, `L` later, `U` undo. Choices save in the browser; export a JSON backup when you stop. The page needs the audit files from your own September replay in `reports/`. Those are private and not included here, so in this repo the launcher stops with a "file not found" error.

### Live relay (not enabled)

```bash
.venv/bin/python watcher.py preview --limit 25
.venv/bin/python watcher.py test-delivery
.venv/bin/python watcher.py enable-background
```

`enable-background` installs a per-user macOS LaunchAgent. The first live poll starts after each channel's latest post, so history is never replayed into the relay. Other controls: `watch`, `status`, `doctor`, `disable-background`, and `retry-delivery <id> --acknowledge-possible-duplicate` for a send whose outcome was uncertain.

## Limits

- Keyword rules match wording, not meaning; gaps and noise are expected.
- No delivery guarantee while the Mac is asleep or offline; the backlog clears over later polls.
- Telegram's API cannot guarantee exactly-once delivery, so uncertain sends are never retried automatically.
- Deleted posts cannot be recovered, and there is no OCR or transcription of media.

## Not built yet

AI-assisted filtering in daily batches, linking each story to its original source article, free unattended hosting for the collector and the public site, the publication pilot and periodic digests.

## Credits and use

Built by iixo3 with Codex. The code is published to show the work: you're welcome to read it, but it is not licensed for reuse.
