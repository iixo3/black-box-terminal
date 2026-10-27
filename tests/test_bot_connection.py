from __future__ import annotations

import io
import json
import runpy
import socket
import ssl
import sys
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from telegram_io import Bot, BotConnectionError, BotError, UncertainDelivery

ROOT = Path(__file__).resolve().parents[1]


class BotCertificateTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin" and Path("/etc/ssl/cert.pem").is_file(),
                         "Requires the macOS system certificate bundle")
    def test_bot_request_works_with_missing_python_roots_and_keeps_tls_verification(self):
        empty_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(empty_context.get_ca_certs(), [])

        def response(request, *, timeout, context=None):
            self.assertIsNotNone(context, "Bot API request must supply a trusted TLS context")
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertGreater(len(context.get_ca_certs()), 0)
            return io.BytesIO(b'{"ok": true, "result": {"id": 123}}')

        with patch("ssl.create_default_context", return_value=empty_context), \
             patch("telegram_io.urlopen", side_effect=response):
            bot = Bot({"bot_token": "123:placeholder", "destination": ""})
            self.assertEqual(bot.request("getMe", {}), {"id": 123})

    def test_certificate_error_during_token_check_is_clear_and_secret_safe(self):
        secret = "123:do_not_print_this"
        failure = URLError(ssl.SSLCertVerificationError(1, f"certificate error at /bot{secret}/getMe"))
        with patch("telegram_io.urlopen", side_effect=failure):
            bot = Bot({"bot_token": secret, "destination": ""})
            with self.assertRaises(BotConnectionError) as raised:
                bot.request("getMe", {})
        self.assertIn("TLS certificate verification failed", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)

    def test_read_network_failures_have_safe_actionable_diagnostics(self):
        cases = [(URLError(TimeoutError("secret timeout URL")), "timed out"),
                 (URLError(socket.gaierror(8, "secret DNS URL")), "hostname could not be resolved"),
                 (ValueError("secret invalid response"), "unreadable response")]
        for failure, expected in cases:
            with self.subTest(expected=expected), patch("telegram_io.urlopen", side_effect=failure):
                bot = Bot({"bot_token": "123:placeholder", "destination": ""})
                with self.assertRaises(BotConnectionError) as raised:
                    bot.request("getMe", {})
                self.assertIn(expected, str(raised.exception))
                self.assertNotIn("secret", str(raised.exception))

    def test_send_failures_remain_uncertain_to_avoid_duplicate_delivery(self):
        for failure in [URLError(TimeoutError("secret")), ValueError("secret invalid response")]:
            with self.subTest(failure=type(failure).__name__), patch("telegram_io.urlopen", side_effect=failure):
                bot = Bot({"bot_token": "123:placeholder", "destination": "-100123"})
                with self.assertRaises(UncertainDelivery) as raised:
                    bot.send("Test")
                self.assertNotIn("secret", str(raised.exception))

    def test_rejected_token_reports_only_safe_api_status(self):
        secret = "123:do_not_print_this"
        response = io.BytesIO(json.dumps({"ok": False, "error_code": 401, "description": secret}).encode())
        failure = HTTPError(f"https://api.telegram.org/bot{secret}/getMe", 401, secret, {}, response)
        with patch("telegram_io.urlopen", side_effect=failure):
            bot = Bot({"bot_token": secret, "destination": ""})
            with self.assertRaises(BotError) as raised:
                bot.request("getMe", {})
        self.assertEqual(raised.exception.code, 401)
        self.assertNotIn(secret, str(raised.exception))

    def test_setup_prints_clear_errors_without_exposing_token(self):
        from unittest.mock import AsyncMock
        for failure, expected in [(BotConnectionError("Bot API TLS certificate verification failed."),
                                   "TLS certificate verification failed"),
                                  (BotError(401), "Telegram rejected the bot token (401)")]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                local = Path(temp)
                credentials = local / "credentials.json"
                credentials.write_text(json.dumps({"api_id": 123, "api_hash": "0" * 32}))
                stderr = io.StringIO()
                login = AsyncMock()
                with patch("watcher.LOCAL", local), patch("watcher.CREDS", credentials), \
                     patch("watcher.SESSION", local / "reader"), \
                     patch("watcher.process_lock", return_value=nullcontext()), \
                     patch("telegram_io.login", login), patch("telegram_io.Bot.request", side_effect=failure), \
                     patch("builtins.input", return_value=""), \
                     patch("getpass.getpass", side_effect=["", "123:placeholder"]), \
                     redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as raised:
                        runpy.run_path(str(ROOT / "setup_local.py"), run_name="__main__")
                self.assertEqual(raised.exception.code, 1)
                self.assertIn(expected, stderr.getvalue())
                self.assertNotIn("123:placeholder", stderr.getvalue())
                login.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
