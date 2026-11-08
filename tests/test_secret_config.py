import os
import sys
import unittest
from unittest.mock import patch

from test_dyknow import make_cfg, run_cli, tmpdir


class TestSecretConfig(unittest.TestCase):
    def test_setting_api_key_does_not_expose_it(self):
        secret = "fixture-not-a-real-key-123456789"
        commands = [
            ["config", "siliconflow_api_key", secret],
            ["config", "--", "siliconflow_api_key", secret],
            ["config", "siliconflow_api_key", "--", secret],
        ]
        for argv in commands:
            with self.subTest(argv=argv):
                cfg = make_cfg(tmpdir())
                with patch.dict(os.environ, {"SILICONFLOW_API_KEY": ""}), patch.object(sys, "argv", ["dyknow", *argv]):
                    code, output, result = run_cli(argv, cfg)
                    self.assertEqual(code, 0)
                    self.assertEqual(cfg.get("siliconflow_api_key"), secret)
                self.assertNotIn(secret, output)
                self.assertNotEqual(result.get("value"), secret)
                self.assertNotIn(secret, (cfg.logs_dir / "dyknow.log").read_text(encoding="utf-8"))

    def test_reading_api_key_does_not_expose_it(self):
        cfg = make_cfg(tmpdir())
        secret = "fixture-not-a-real-key-987654321"
        cfg.set("siliconflow_api_key", secret)
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": ""}):
            code, output, result = run_cli(["config", "siliconflow_api_key"], cfg)
        self.assertEqual(code, 0)
        self.assertNotIn(secret, output)
        self.assertNotIn(secret, (cfg.logs_dir / "dyknow.log").read_text(encoding="utf-8"))