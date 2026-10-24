from __future__ import annotations

import unittest

from mouthtts import textutil


class TextUtilTests(unittest.TestCase):
    def test_clean_removes_control_chars(self) -> None:
        raw = "你好\x00，世界\x0b！\r\n第二行"
        self.assertEqual(textutil.clean(raw), "你好，世界！\n第二行")

    def test_clean_collapses_blank_lines(self) -> None:
        self.assertEqual(textutil.clean("a\n\n\n\nb"), "a\n\nb")

    def test_char_count_ignores_whitespace(self) -> None:
        self.assertEqual(textutil.char_count("你 好\n世界\t!"), 5)

    def test_short_text_is_one_chunk(self) -> None:
        text = "你好，世界。"
        self.assertEqual(textutil.split_for_speech(text), [text])

    def test_long_text_split_at_sentence_boundaries(self) -> None:
        sentence = "这是一句测试文字。" * 10
        text = sentence * 30  # 900 字
        chunks = textutil.split_for_speech(text, max_chars=120)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))
        self.assertEqual("".join(chunks), text)

    def test_single_long_sentence_is_hard_split(self) -> None:
        text = "啊" * 500
        chunks = textutil.split_for_speech(text, max_chars=100)
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))
        self.assertEqual("".join(chunks), text)

    def test_split_sentences_keeps_punctuation(self) -> None:
        self.assertEqual(
            textutil.split_sentences("你好。世界！"), ["你好。", "世界！"]
        )

    def test_empty_text(self) -> None:
        self.assertEqual(textutil.split_for_speech("   "), [])


if __name__ == "__main__":
    unittest.main()
