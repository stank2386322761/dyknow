import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dyknow import cli, login
from dyknow.config import Config
from test_dyknow import run_cli, make_cfg
import fake_douyin
from fake_douyin import FakeDouyin


class TestFreshQr(unittest.TestCase):
    def test_login_success_waits_for_worker_to_release_browser(self):
        from dyknow.login import session
        with tempfile.TemporaryDirectory() as temp:
            cfg = Config(home=Path(temp))
            session._write_status(cfg, state=login.LOGGED_IN, pid=123456)
            with patch.object(session, "_alive", side_effect=[True, False]) as alive, \
                 patch.object(session.time, "sleep"):
                result = login.wait(cfg, timeout=3)
            self.assertEqual(result["state"], login.LOGGED_IN)
            self.assertGreaterEqual(alive.call_count, 2)

    def test_expired_code_is_not_refreshed_before_user_requests_it(self):
        with tempfile.TemporaryDirectory() as temp, FakeDouyin(1, login_page=True) as fake:
            cfg = make_cfg(Path(temp), fake.base, headless=False)
            with patch.dict(os.environ, {"DYKNOW_REMOTE": "1", "DYKNOW_NO_AUTO_INSTALL": "1"}):
                try:
                    code, _, first = run_cli(["login"], cfg)
                    self.assertEqual(code, cli.WAIT_USER)
                    fake.qr_state = "expired"
                    deadline = time.time() + 12
                    while time.time() < deadline and login.read_status(cfg).get("state") != "qr_expired":
                        time.sleep(0.2)
                    self.assertEqual(login.read_status(cfg).get("state"), "qr_expired")
                    self.assertEqual(fake.requests.count("/test/refresh"), 0)
                    self.assertTrue(Path(first["qr"]).exists())
                finally:
                    login.stop_worker(cfg)

    def test_refresh_reaches_worker_and_produces_a_distinct_image(self):
        page = fake_douyin.LOGIN_PAGE.replace('let gen = 0, smsOpt = false;',
            "let gen = Number(sessionStorage.gen || 0); sessionStorage.gen = gen + 1; let smsOpt = false;")
        with patch.object(fake_douyin, "LOGIN_PAGE", page), tempfile.TemporaryDirectory() as temp, FakeDouyin(1, login_page=True) as fake:
            cfg = make_cfg(Path(temp), fake.base, headless=False)
            with patch.dict(os.environ, {"DYKNOW_REMOTE": "1", "DYKNOW_NO_AUTO_INSTALL": "1"}):
                try:
                    code, _, first = run_cli(["login"], cfg)
                    self.assertEqual(code, cli.WAIT_USER)
                    old = Path(first["qr"]).read_bytes()
                    code, output, fresh = run_cli(["login", "--refresh"], cfg)
                    self.assertEqual(code, cli.WAIT_USER, output)
                    self.assertNotEqual(first["qr"], fresh["qr"])
                    self.assertNotEqual(old, Path(fresh["qr"]).read_bytes())
                finally:
                    login.stop_worker(cfg)

    def test_explicit_refresh_waits_for_a_new_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = Config(home=Path(temp), values={"output_dir": str(Path(temp) / "notes")})
            state_dir = login.state_dir(cfg)
            state_dir.mkdir()
            old = {"state": login.QR_READY, "pid": 1, "qr_version": 4, "qr": str(state_dir / "qr-4.png")}
            new = {**old, "qr_version": 5, "qr": str(state_dir / "qr-5.png")}
            def status(_cfg):
                request = state_dir / "refresh.txt"
                return {**new, "refresh_token": request.read_text()} if request.exists() else old
            with patch("dyknow.login.session.read_status", side_effect=status), \
                 patch("dyknow.login.session._alive", return_value=True), \
                 patch("dyknow.login.session.time.sleep"):
                result = login.refresh_qr(cfg, timeout=2)
            self.assertEqual(result["qr_version"], 5)
            self.assertTrue((state_dir / "refresh.txt").exists())
            self.assertEqual(result["qr"], str(state_dir / "qr-5.png"))

    def test_login_refresh_command_returns_new_image(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = Config(home=Path(temp), values={"output_dir": str(Path(temp) / "notes")})
            st = {"state": login.QR_READY, "qr_version": 5, "qr": str(Path(temp) / "qr-5.png")}
            with patch("dyknow.login.refresh_qr", return_value=st) as refresh:
                code, _, result = run_cli(["login", "--refresh"], cfg)
            self.assertEqual(code, cli.WAIT_USER)
            self.assertEqual(result["qr"], st["qr"])
            refresh.assert_called_once()
