from __future__ import annotations

import time
import unittest

from mouthtts import voices


class VoicesTests(unittest.TestCase):
    def test_curated_options_are_unique(self) -> None:
        options = voices.curated_options()
        self.assertGreaterEqual(len(options), 10)
        self.assertEqual(len({short for _label, short in options}), len(options))

    def test_curated_display(self) -> None:
        label = voices.curated_display("zh-CN-XiaoxiaoNeural")
        self.assertIn("晓晓", label)
        self.assertIn("普通话", label)

    def test_unknown_voice_display_is_short_name(self) -> None:
        self.assertEqual(
            voices.curated_display("xx-XX-UnknownNeural"), "xx-XX-UnknownNeural"
        )

    def test_locale_label(self) -> None:
        self.assertEqual(voices.locale_label("zh-CN"), "普通话")
        self.assertEqual(voices.locale_label("zh-HK"), "粤语")
        self.assertEqual(voices.locale_label("en-GB"), "英语（英国）")

    def test_presets_contain_valid_voices(self) -> None:
        known = {short for _label, short in voices.curated_options()}
        for preset in voices.BUILTIN_PRESETS:
            self.assertIn(preset["voice"], known)
            self.assertTrue(preset["name"])
            self.assertIn(preset["rate"], range(-50, 101))

    def test_find_and_builtin(self) -> None:
        preset = voices.find_preset("温柔女声 · 晓晓", [])
        self.assertIsNotNone(preset)
        self.assertTrue(voices.is_builtin("温柔女声 · 晓晓"))
        user = [{"name": "自定义", "voice": "v", "rate": 0, "pitch": 0, "volume": 0}]
        self.assertEqual(voices.find_preset("自定义", user)["voice"], "v")
        self.assertFalse(voices.is_builtin("自定义"))

    def test_merge_curated_includes_chinese(self) -> None:
        merged = voices.merge_curated(
            [{"ShortName": "en-US-AriaNeural", "Locale": "en-US"}]
        )
        short_names = {row["ShortName"] for row in merged}
        self.assertIn("en-US-AriaNeural", short_names)
        self.assertIn("zh-CN-XiaoxiaoNeural", short_names)

    def test_display_name_for_curated(self) -> None:
        self.assertIn(
            "晓晓",
            voices.display_name({"ShortName": "zh-CN-XiaoxiaoNeural"}),
        )

    def test_cache_age_text(self) -> None:
        self.assertEqual(voices.cache_age_text(0), "未缓存")
        self.assertIn("分钟", voices.cache_age_text(time.time() - 120))


if __name__ == "__main__":
    unittest.main()
