from __future__ import annotations

import json
import os
import shutil
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mouthtts import config

TEST_ROOT = Path(__file__).resolve().parents[1] / ".test-tmp"


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        # 不用 tempfile.mkdtemp：某些受限环境下 0700 目录会不可访问
        self.dir = TEST_ROOT / f"cfg-{os.getpid()}-{time.time_ns()}"
        self.dir.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self._patches = [
            patch.object(config, "DATA_DIR", self.dir),
            patch.object(config, "LOG_DIR", self.dir / "logs"),
            patch.object(config, "CONFIG_PATH", self.dir / "config.json"),
            patch.object(config, "VOICE_CACHE_PATH", self.dir / "voices.json"),
        ]
        for patcher in self._patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_defaults(self) -> None:
        cfg = config.normalize({})
        self.assertEqual(cfg["voice"], config.DEFAULTS["voice"])
        self.assertEqual(cfg["rate"], 0)
        self.assertEqual(cfg["presets"], [])

    def test_clamp_and_types(self) -> None:
        cfg = config.normalize(
            {
                "rate": 999,
                "pitch": -999,
                "volume": "abc",
                "prebuffer_ms": 9999,
                "text": "x" * 30000,
            }
        )
        self.assertEqual(cfg["rate"], config.RATE_MAX)
        self.assertEqual(cfg["pitch"], config.PITCH_MIN)
        self.assertEqual(cfg["volume"], 0)
        self.assertEqual(cfg["prebuffer_ms"], config.PREBUFFER_MAX)
        self.assertEqual(len(cfg["text"]), 20000)

    def test_device_normalization(self) -> None:
        self.assertIsNone(config.normalize({"device": "bad"})["device"])
        self.assertIsNone(config.normalize({"device": {"name": ""}})["device"])
        cfg = config.normalize(
            {"device": {"hostapi": "Windows WASAPI", "name": "CABLE Input"}}
        )
        self.assertEqual(cfg["device"]["name"], "CABLE Input")

    def test_presets_filtered(self) -> None:
        cfg = config.normalize(
            {
                "presets": [
                    {"name": "ok", "voice": "v1", "rate": 9, "pitch": 9},
                    {"name": "", "voice": "v2"},
                    {"voice": "v3"},
                    "nope",
                ]
            }
        )
        self.assertEqual(len(cfg["presets"]), 1)
        self.assertEqual(cfg["presets"][0]["rate"], 9)

    def test_save_load_roundtrip(self) -> None:
        cfg = config.normalize(
            {
                "voice": "zh-CN-YunxiNeural",
                "rate": 12,
                "device": {"hostapi": "MME", "name": "Speakers"},
                "presets": [{"name": "我的", "voice": "zh-CN-YunxiNeural"}],
            }
        )
        config.save(cfg)
        loaded = config.load()
        self.assertEqual(loaded["voice"], "zh-CN-YunxiNeural")
        self.assertEqual(loaded["rate"], 12)
        self.assertEqual(loaded["device"]["hostapi"], "MME")
        self.assertEqual(loaded["presets"][0]["name"], "我的")

    def test_corrupt_config_is_backed_up(self) -> None:
        config.ensure_dirs()
        config.CONFIG_PATH.write_text("{not json", encoding="utf-8")
        cfg = config.load()
        self.assertEqual(cfg["voice"], config.DEFAULTS["voice"])
        backups = list(self.dir.glob("config.broken-*.json"))
        self.assertEqual(len(backups), 1)

    def test_voice_cache_roundtrip(self) -> None:
        rows = [{"ShortName": "zh-CN-XiaoxiaoNeural", "Locale": "zh-CN"}]
        config.save_voice_cache(rows)
        self.assertEqual(config.load_voice_cache(), rows)
        self.assertTrue(config.VOICE_CACHE_PATH.exists())

    def test_voice_cache_ignores_invalid(self) -> None:
        config.ensure_dirs()
        config.VOICE_CACHE_PATH.write_text("null", encoding="utf-8")
        self.assertEqual(config.load_voice_cache(), [])
        config.VOICE_CACHE_PATH.write_text(json.dumps({"voices": [{"x": 1}]}))
        self.assertEqual(config.load_voice_cache(), [])


if __name__ == "__main__":
    unittest.main()
