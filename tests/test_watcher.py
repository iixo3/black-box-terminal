from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core import Deduplicator, Item, Link, Message, Rules, delivery_parts, evaluate, split_message, utf16_size
from reports import write_report
from store import Store
from telegram_io import Bot, BotError, Reader, UncertainDelivery, convert, deliver
import watcher

ROOT = Path(__file__).resolve().parents[1]
DATE = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


def message(text, source="WatcherGuru", message_id=1, **kwargs):
    return Message(source, message_id, DATE, text, **kwargs)


class FilteringTests(unittest.TestCase):
    def setUp(self):
        self.rules = Rules.load(ROOT / "config.json")

    def assess(self, text):
        return self.rules.assess(split_message(message(text))[0])

    def test_product_scope_and_unrelated_news(self):
        cases = {
            "A bank launches stablecoin settlement for institutional clients": "keep",
            "A fintech introduces tokenized stock investments for retail users": "keep",
            "Morpho launches new lending vaults": "keep",
            "Regulator approves crypto custody license": "keep",
            "A crypto exchange acquires a custody provider": "keep",
            "Blockchain network releases an interoperability upgrade": "keep",
            "DeFi report shows vault TVL growth": "keep",
            "A blockchain bridge suspends withdrawals after an exploit": "keep",
            "A new onchain lending startup raises $20 million": "keep",
            "Decentralized finance launches a new lending product": "keep",
            "Bank pilots DLT settlement for institutional clients": "keep",
            "The president announces a new foreign policy": "drop",
            "Ten-year treasury yield rises": "drop",
            "Bank launches generic savings account": "drop",
            "A celebrity bought a new car": "drop",
            "Bitcoin surpasses $100,000": "drop",
            "Strategy buys 100 Bitcoin": "drop",
            "Bitcoin ETF inflows hit a record": "drop",
            "Join our VIP crypto trading group for a buy signal": "drop",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                decision = self.assess(text)
                self.assertEqual(decision.status, expected)
                self.assertTrue(decision.reason)

    def test_material_event_overrides_routine_price_or_flow(self):
        self.assertEqual(self.assess("Bitcoin surpasses $100,000 as bank launches a crypto payments product").status, "keep")
        self.assertEqual(self.assess("New crypto ETF approval follows record inflows").status, "keep")

    def test_url_does_not_create_relevance(self):
        self.assertEqual(self.assess("A celebrity changes hairstyle https://example.com/crypto-stablecoin").status, "drop")

    def test_matched_terms_explain_approval(self):
        decision = self.assess("ExampleBank launches stablecoin settlement")
        self.assertIn("stablecoin", decision.matches["Stablecoins"])

    def test_media_and_protected_content_remain_auditable(self):
        decisions = evaluate([message("", media_present=True),
                              message("Bank launches stablecoin payments", message_id=2, protected=True)], self.rules)
        self.assertEqual([(d.status, d.reason) for d in decisions],
                         [("drop", "media_only_no_text"), ("hold", "source_prohibits_forwarding")])

    def test_album_caption_and_links_survive_without_blank_news_entries(self):
        text = "🦙 Bank launches stablecoin payments"
        raw = SimpleNamespace(message=text, id=10, date=DATE, entities=[],
                              media=object(), grouped_id=123)
        caption = convert("WatcherGuru", raw)
        caption.links = [Link(0, len(text), "https://example.com/announcement")]
        parts = [caption, message("", message_id=11, media_present=True, grouped_id=123),
                 message("", message_id=12, media_present=True, grouped_id=123)]
        decisions = evaluate(parts, self.rules)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].status, "keep")
        self.assertEqual(decisions[0].item.text, text)
        self.assertEqual(decisions[0].item.links, ["https://example.com/announcement"])
        self.assertEqual(decisions[0].item.grouped_id, 123)
        self.assertEqual(Message.from_record(caption.record()).grouped_id, 123)
        legacy = caption.record()
        del legacy["grouped_id"]
        self.assertIsNone(Message.from_record(legacy).grouped_id)

    def test_album_parts_do_not_depend_on_batch_order_or_adjacent_messages(self):
        attachment = message("", message_id=2, media_present=True, grouped_id=123)
        caption = message("Bank launches stablecoin payments", grouped_id=123)
        self.assertEqual(evaluate([attachment], self.rules), [])
        self.assertEqual(len(evaluate([caption], self.rules)), 1)
        # Nonempty captions, even in the same album, must never be discarded.
        second = message("Bank launches crypto custody", message_id=3, grouped_id=123)
        self.assertEqual(len(evaluate([attachment, second, caption], self.rules)), 2)
        # Never guess an album from a neighbouring message or timestamp.
        standalone = message("", message_id=4, media_present=True)
        self.assertEqual(len(evaluate([caption, standalone], self.rules)), 2)

    def test_roundup_split_preserves_links_and_wrapped_text(self):
        text = "News Round-Up\nDeFi 🏦\n→ Morpho launches a vault\n  with institutional access\n→ A celebrity changes hairstyle\nReads 📚\n→ Study of stablecoin adoption"
        start = text.index("Morpho")
        raw = message(text, "defillama_tg", links=[Link(start, start + 6, "https://example.com/vault")])
        items = split_message(raw)
        self.assertEqual(len(items), 3)
        self.assertIn("institutional access", items[0].text)
        self.assertEqual(items[0].links, ["https://example.com/vault"])
        self.assertEqual(items[1].links, [])
        self.assertEqual(items[2].context, "Reads 📚")
        self.assertEqual([d.status for d in evaluate([raw], self.rules)], ["keep", "drop", "keep"])

    def test_hidden_links_after_emoji_use_correct_utf16_offsets(self):
        text = "🦙 News\n→ Morpho vault\n→ Other story"
        start = text.index("Morpho")
        entity = type("MessageEntityTextUrl", (), {"offset": utf16_size(text[:start]),
                      "length": utf16_size("Morpho"), "url": "https://example.com/vault"})()
        raw = SimpleNamespace(message=text, id=1, date=DATE, entities=[entity], media=None)
        converted = convert("defillama_tg", raw)
        self.assertEqual(converted.links[0].start, start)
        self.assertEqual(split_message(converted)[0].links, ["https://example.com/vault"])

    def test_product_feature_bullets_are_not_split_as_roundup(self):
        raw = message("Crypto wallet launches:\n→ Pay\n→ Save\n→ Trade")
        self.assertEqual(len(split_message(raw)), 1)

    def test_duplicates_preserve_different_entities_and_numbers(self):
        raw = [message("JUST IN: AlphaBank launches stablecoin payments", message_id=1),
               message("AlphaBank launches stablecoin payments", "TreeNewsFeed", 2),
               message("BetaBank launches stablecoin payments", "AGGRNEWSWIRE", 3),
               message("Crypto exchange raises $50 million", "ZoomerfiedNews", 4),
               message("Crypto exchange raises $500 million", "WatcherGuru", 5)]
        decisions = evaluate(raw, self.rules)
        by_key = {d.item.key: d for d in decisions}
        self.assertEqual(sum(d.status == "duplicate" for d in decisions), 1)
        self.assertEqual(by_key["aggrnewswire:3:0"].status, "keep")
        self.assertEqual(by_key["watcherguru:5:0"].status, "keep")

    def test_duplicate_window_expires(self):
        one = message("Bank launches stablecoin payments")
        two = message(one.text, message_id=2)
        two.date = DATE + timedelta(days=8)
        self.assertEqual([d.status for d in evaluate([one, two], self.rules)], ["keep", "keep"])

    def test_dedup_preserves_entity_roles_and_currencies(self):
        raw = [message("BankAlpha acquires BankBeta to expand stablecoin payment infrastructure", message_id=1),
               message("BankBeta acquires BankAlpha to expand stablecoin payment infrastructure", message_id=2),
               message("Crypto exchange raises $50 million", message_id=3),
               message("Crypto exchange raises €50 million", message_id=4)]
        self.assertTrue(all(d.status == "keep" for d in evaluate(raw, self.rules)))

    def test_long_messages_and_emoji_are_not_truncated(self):
        item = split_message(message("🦙" * 4000 + " stablecoin launch",
                                     links=[Link(0, 2, "https://example.com/hidden")]))[0]
        parts = delivery_parts(item)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(utf16_size(part) <= 4096 for part in parts))
        reassembled = "".join(part.split("\n", 1)[1] for part in parts)
        self.assertIn(item.text, reassembled)
        self.assertIn("https://example.com/hidden", reassembled)
        self.assertIn(item.source_url, reassembled)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "archive.sqlite")
        self.rules = Rules.load(ROOT / "config.json")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def live(self, raw):
        run = self.store.new_run("live", self.rules.version, {})
        self.store.save(run, [raw], evaluate([raw], self.rules), {}, {raw.source: raw.message_id})
        return run

    def test_checkpoint_outbox_commit_and_repeated_save(self):
        raw = message("Bank launches stablecoin payments")
        self.live(raw)
        self.live(raw)
        self.assertEqual(self.store.checkpoint(raw.source), 1)
        self.assertEqual(self.store.status()["delivery_counts"], {"pending": 1})

    def test_album_split_across_polls_keeps_caption_once_and_archives_all_parts(self):
        caption = message("Bank launches stablecoin payments", grouped_id=123)
        attachment = message("", message_id=2, media_present=True, grouped_id=123)
        first = self.live(caption)
        second = self.live(attachment)
        self.assertEqual(self.store.checkpoint("WatcherGuru"), 2)
        self.assertEqual(self.store.status()["archived_messages"], 2)
        self.assertEqual(self.store.status()["delivery_counts"], {"pending": 1})
        self.assertEqual(len(self.store.report_data(first)["decisions"]), 1)
        self.assertEqual(self.store.report_data(second)["decisions"], [])
        self.assertEqual(self.store.report_data(second)["messages"][0]["grouped_id"], 123)
        # Caption arriving in a later poll also survives without a pending-album buffer.
        self.live(message("", message_id=3, media_present=True, grouped_id=456))
        self.live(message("Bank launches crypto custody", message_id=4, grouped_id=456))
        self.assertEqual(self.store.checkpoint("WatcherGuru"), 4)
        self.assertEqual(self.store.status()["delivery_counts"], {"pending": 2})

    def test_transaction_failure_does_not_advance_or_partially_archive(self):
        raw = message("Bank launches stablecoin payments")
        decision = evaluate([raw], self.rules)[0]
        decision.matches["invalid"] = {object()}
        run = self.store.new_run("live", self.rules.version, {})
        with self.assertRaises(TypeError):
            self.store.save(run, [raw], [decision], {}, {raw.source: 1})
        self.assertIsNone(self.store.checkpoint(raw.source))
        self.assertEqual(self.store.status()["archived_messages"], 0)
        self.assertEqual(self.store.status()["delivery_counts"], {})

    def test_replay_never_seeds_live_dedup_or_outbox(self):
        raw = message("Bank launches stablecoin payments")
        run = self.store.new_run("replay", self.rules.version, {})
        self.store.save(run, [raw], evaluate([raw], self.rules), {})
        self.assertIsNone(self.store.checkpoint(raw.source))
        self.assertEqual(self.store.status()["delivery_counts"], {})
        dedup = self.store.deduplicator(7, DATE - timedelta(days=7))
        self.assertEqual(dedup.apply(self.rules.assess(split_message(raw)[0])).status, "keep")

    def test_restart_uses_live_dedup_and_recovers_uncertain_send(self):
        raw = message("Bank launches stablecoin payments")
        self.live(raw)
        self.store.delivery_status("watcherguru:1:0", 0, "sending")
        self.assertEqual(self.store.recover_sending(), 1)
        self.assertEqual(self.store.status()["delivery_counts"], {"uncertain": 1})
        self.assertIsNone(self.store.next_delivery())
        dedup = self.store.deduplicator(7, DATE - timedelta(days=7))
        self.assertEqual(dedup.apply(self.rules.assess(split_message(raw)[0])).status, "duplicate")

    def test_report_contains_rejected_raw_posts_and_rule_snapshot(self):
        raw = [message("Unrelated hairstyle <script>alert(1)</script>"),
               message("Bank launches stablecoin payments", message_id=2)]
        run = self.store.new_run("replay", self.rules.version, {})
        self.store.save(run, raw, evaluate(raw, self.rules), {"WatcherGuru": {"status": "ok", "messages": 2}})
        data = self.store.report_data(run)
        self.assertEqual(len(data["messages"]), 2)
        report = write_report(data, self.rules.config, Path(self.temp.name) / "reports")
        rendered = report.read_text()
        self.assertIn("no_crypto_or_direct_topic_context", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertTrue(report.with_suffix(".rules.json").exists())
        self.assertEqual(len(report.with_suffix(".messages.jsonl").read_text().splitlines()), 2)

    def test_edited_post_does_not_change_old_run_snapshot(self):
        raw = message("Bank launches stablecoin payments")
        run = self.live(raw)
        edited = message("Bank launches tokenized stock trading")
        self.live(edited)
        self.assertEqual(self.store.report_data(run)["messages"][0]["text"], raw.text)

    def test_offline_replay_cli_cannot_invoke_bot_or_change_live_state(self):
        self.live(message("Bank launches stablecoin payments", message_id=100))
        initial = self.store.status()
        args = SimpleNamespace(start="2026-09-01", end="2026-10-01", input=str(ROOT / "examples/demo.jsonl"))
        with patch("telegram_io.Bot", side_effect=AssertionError("Replay must never use sender")), \
             patch("telegram_io.load_credentials", side_effect=AssertionError("Offline replay must not read secrets")), \
             patch.object(watcher, "ROOT", Path(self.temp.name)), patch.object(watcher, "LOCAL", Path(self.temp.name)):
            self.assertEqual(asyncio.run(watcher.historical(args, self.store, self.rules)), 0)
        self.assertEqual(self.store.status()["checkpoints"], initial["checkpoints"])
        self.assertEqual(self.store.status()["delivery_counts"], initial["delivery_counts"])


class NetworkBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_source_fetch_does_not_advance_checkpoint(self):
        async def failing_iterator(*args, **kwargs):
            yield SimpleNamespace(message="Stablecoin launch", id=42, date=DATE, entities=[], media=None)
            raise RuntimeError("Simulated disconnect")
        client = Mock()
        client.iter_messages = failing_iterator
        with patch("telegram_io.new_client", return_value=client):
            reader = Reader({}, Path("unused"), ["WatcherGuru"])
        reader.entity = AsyncMock(return_value=SimpleNamespace(noforwards=False))
        messages, coverage, updates = await reader.collect_live({"WatcherGuru": 41})
        self.assertEqual(messages, [])
        self.assertNotIn("WatcherGuru", updates)
        self.assertEqual(coverage["WatcherGuru"]["status"], "error")

    async def test_cold_start_initializes_without_historical_relay(self):
        client = Mock()
        client.get_messages = AsyncMock(return_value=[SimpleNamespace(id=42)])
        with patch("telegram_io.new_client", return_value=client):
            reader = Reader({}, Path("unused"), ["WatcherGuru"])
        reader.entity = AsyncMock(return_value=SimpleNamespace(noforwards=False))
        messages, coverage, updates = await reader.collect_live({"WatcherGuru": None})
        self.assertEqual(messages, [])
        self.assertEqual(updates, {"WatcherGuru": 42})
        self.assertEqual(coverage["WatcherGuru"]["status"], "initialized")
        client.iter_messages.assert_not_called()

    async def test_historical_boundaries_fetch_irrelevant_and_empty_posts(self):
        async def history(*args, **kwargs):
            for identifier, date, text in [(3, DATE, "Unrelated story"), (2, DATE - timedelta(hours=1), ""),
                                           (1, DATE - timedelta(days=10), "Too old")]:
                yield SimpleNamespace(id=identifier, date=date, message=text, entities=[], media=None)
        client = Mock()
        client.iter_messages = history
        with patch("telegram_io.new_client", return_value=client):
            reader = Reader({}, Path("unused"), ["WatcherGuru"])
        reader.entity = AsyncMock(return_value=SimpleNamespace(noforwards=False))
        messages, coverage = await reader.collect_range(DATE - timedelta(days=1), DATE + timedelta(days=1))
        self.assertEqual([m.message_id for m in messages], [3, 2])
        self.assertEqual(coverage["WatcherGuru"]["messages"], 2)

    async def test_timeout_is_held_and_rate_limit_is_retryable(self):
        for failure, expected in [(UncertainDelivery("unknown"), "uncertain"), (BotError(429, 120), "pending")]:
            with tempfile.TemporaryDirectory() as temp:
                store = Store(Path(temp) / "archive.sqlite")
                try:
                    rules = Rules.load(ROOT / "config.json")
                    raw = message("Bank launches stablecoin payments")
                    run = store.new_run("live", rules.version, {})
                    store.save(run, [raw], evaluate([raw], rules), {}, {raw.source: 1})
                    bot = Mock()
                    bot.send.side_effect = failure
                    await deliver(store, bot, 1)
                    self.assertEqual(store.status()["delivery_counts"], {expected: 1})
                    self.assertIsNone(store.next_delivery())
                finally:
                    store.close()


class BotAndDateTests(unittest.TestCase):
    def test_public_destination_rejected_before_sending(self):
        bot = Bot({"bot_token": "123:placeholder", "destination": "-100123"})
        bot.request = Mock(side_effect=[{"id": 123}, {"type": "channel", "username": "public_channel"}])
        with self.assertRaises(ValueError):
            bot.preflight()
        self.assertEqual(bot.request.call_count, 2)

    def test_local_month_is_correct_utc_interval(self):
        # Etc/GMT-2 is the IANA name for a fixed UTC+2 zone.
        start, end = watcher.date_range("2026-09-01", "2026-10-01", "Etc/GMT-2")
        self.assertEqual(start.isoformat(), "2026-08-31T22:00:00+00:00")
        self.assertEqual(end.isoformat(), "2026-09-30T22:00:00+00:00")
        with self.assertRaises(ValueError):
            watcher.date_range("2026-10-01", "2026-09-01", "Etc/GMT-2")


if __name__ == "__main__":
    unittest.main()
