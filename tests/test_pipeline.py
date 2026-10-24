from __future__ import annotations

import unittest

from mouthtts.pipeline import LiveSink


class LiveSinkTests(unittest.TestCase):
    def test_feed_read_partial(self) -> None:
        sink = LiveSink()
        sink.feed(b"abcdef")
        self.assertEqual(sink.read(4), b"abcd")
        self.assertEqual(sink.read(10), b"ef")
        self.assertEqual(sink.total_bytes, 6)

    def test_finish_yields_eof_after_buffer(self) -> None:
        sink = LiveSink()
        sink.feed(b"12")
        sink.finish()
        self.assertEqual(sink.read(10), b"12")
        self.assertEqual(sink.read(10), b"")

    def test_close_discards_buffer_and_returns_eof(self) -> None:
        sink = LiveSink()
        sink.feed(b"data")
        sink.close()
        self.assertEqual(sink.read(10), b"")
        sink.feed(b"late")
        self.assertEqual(sink.read(10), b"")

    def test_empty_feed_is_noop(self) -> None:
        sink = LiveSink()
        sink.feed(b"")
        self.assertEqual(sink.total_bytes, 0)


if __name__ == "__main__":
    unittest.main()
