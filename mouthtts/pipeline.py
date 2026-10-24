"""合成 → 解码 → 播放 的流水线。

* 网络线程把 Edge 的 MP3 分片塞进 ``LiveSink``；
* 播放线程用 miniaudio 增量解码，再以 10 ms 小块写进 PortAudio；
* 起播前只攒 60 ms 的缓冲，兼顾低延迟与不爆音。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import miniaudio

from . import audio
from .edge_engine import DONE_EVENT, ERROR_EVENT, FIRST_CHUNK_EVENT, EdgeEngine

log = logging.getLogger(__name__)

IDLE = "idle"
CONNECTING = "connecting"
SYNTHESIZING = "synthesizing"
SPEAKING = "speaking"
STOPPED = "stopped"
ERROR = "error"


class LiveSink(miniaudio.StreamableSource):
    """线程安全的字节队列：网络线程写，播放线程读。"""

    def __init__(self) -> None:
        super().__init__()
        self._buffer = bytearray()
        self._cond = threading.Condition()
        self._eof = False
        self._closed = False
        self.total_bytes = 0

    def feed(self, data: bytes) -> None:
        if not data:
            return
        with self._cond:
            if self._closed:
                return
            self._buffer += data
            self.total_bytes += len(data)
            self._cond.notify_all()

    def finish(self) -> None:
        with self._cond:
            self._eof = True
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._buffer.clear()
            self._cond.notify_all()

    def read(self, n: int) -> bytes:
        with self._cond:
            while not self._buffer and not self._eof and not self._closed:
                self._cond.wait(timeout=0.1)
            if self._closed:
                return b""
            if self._eof and not self._buffer:
                return b""
            data = bytes(self._buffer[:n])
            del self._buffer[:n]
            return data


class AudioPlayer(threading.Thread):
    def __init__(
        self,
        sink: LiveSink,
        device: audio.OutputDevice,
        prebuffer_ms: int,
        on_first_audio: Callable[[float], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        super().__init__(name="tts-player", daemon=True)
        self.sink = sink
        self.device = device
        self.prebuffer_ms = max(0, min(300, int(prebuffer_ms)))
        self.on_first_audio = on_first_audio
        self.on_done = on_done
        self.on_error = on_error
        self._stop = threading.Event()
        self._stream = None
        self._started_audio = False

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        self.sink.close()
        stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass

    def _first(self) -> None:
        if self._started_audio:
            return
        self._started_audio = True
        try:
            self.on_first_audio(time.perf_counter())
        except Exception:
            log.debug("first-audio 回调异常", exc_info=True)

    def run(self) -> None:
        stream = None
        try:
            stream = audio.open_stream(self.device)
            self._stream = stream
            stream.start()
            channels = min(2, max(1, self.device.channels))
            samplerate = self.device.samplerate
            frame_bytes = channels * 2
            chunk_bytes = max(frame_bytes, int(samplerate * 0.010) * frame_bytes)
            prebuffer_bytes = max(
                frame_bytes, int(samplerate * self.prebuffer_ms / 1000) * frame_bytes
            )

            pending = bytearray()
            first_write = True
            generator = miniaudio.stream_any(
                self.sink,
                miniaudio.FileFormat.MP3,
                miniaudio.SampleFormat.SIGNED16,
                nchannels=channels,
                sample_rate=samplerate,
                frames_to_read=max(1, int(samplerate * 0.010)),
            )
            for pcm in generator:
                if self._stop.is_set():
                    break
                pending += pcm.tobytes()
                if not self._started_audio:
                    if len(pending) < prebuffer_bytes:
                        continue
                    self._first()
                while len(pending) >= chunk_bytes:
                    if first_write:
                        first_write = False
                        self._first()
                    stream.write(bytes(pending[:chunk_bytes]))
                    del pending[:chunk_bytes]

            if pending and not self._stop.is_set():
                self._first()
                stream.write(bytes(pending))
            if not self._stop.is_set():
                # 给 PortAudio 一点时间把缓冲区里的尾巴放完
                latency = getattr(stream, "latency", 0.08) or 0.08
                try:
                    latency = float(latency)
                except (TypeError, ValueError):
                    latency = 0.08
                time.sleep(min(0.30, max(0.06, latency + 0.04)))
        except Exception as exc:
            if not self._stop.is_set():
                log.warning("播放失败: %s", exc, exc_info=True)
                try:
                    self.on_error(f"播放失败：{exc}")
                except Exception:
                    pass
        finally:
            self._stream = None
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if not self._stop.is_set():
                try:
                    self.on_done()
                except Exception:
                    log.debug("播放完成回调异常", exc_info=True)


class TTSPipeline:
    """把引擎和播放器串起来，并通过 ``emit`` 汇报状态。"""

    def __init__(self, emit: Callable[[str, dict[str, Any]], None]) -> None:
        self._emit = emit
        self.engine = EdgeEngine(self._on_engine_event)
        self._session = 0
        self._player: AudioPlayer | None = None
        self._sink: LiveSink | None = None
        self._state = IDLE
        self._started_at = 0.0
        self._lock = threading.Lock()

    # -- 生命周期 ---------------------------------------------------------

    def start(self) -> None:
        self.engine.start()

    def shutdown(self) -> None:
        self.stop(wait=True)
        self.engine.shutdown()

    @property
    def state(self) -> str:
        return self._state

    # -- 控制 -------------------------------------------------------------

    def speak(
        self,
        text: str,
        voice: str,
        rate: int,
        pitch: int,
        volume: int,
        device: audio.OutputDevice,
        prebuffer_ms: int = 60,
    ) -> None:
        with self._lock:
            self._session += 1
            session = self._session
            self._stop_locked(wait=True)
            if not text.strip():
                self._emit(ERROR, {"message": "请先输入要朗读的文字"})
                return
            if device is None:
                self._emit(ERROR, {"message": "没有可用的输出设备，请检查声卡"})
                return

            sink = LiveSink()
            player = AudioPlayer(
                sink=sink,
                device=device,
                prebuffer_ms=prebuffer_ms,
                on_first_audio=lambda ts: self._player_first_audio(session, ts),
                on_done=lambda: self._player_done(session),
                on_error=lambda message: self._player_error(session, message),
            )
            self._sink = sink
            self._player = player
            self._started_at = time.perf_counter()
            self._state = CONNECTING
            self._emit("state", {"state": CONNECTING})
            player.start()
            self.engine.speak(
                text,
                voice,
                rate,
                pitch,
                volume,
                sink,
                self._started_at,
            )

    def stop(self, wait: bool = False) -> None:
        with self._lock:
            self._stop_locked(wait=wait)

    def _stop_locked(self, wait: bool) -> None:
        player = self._player
        if player is not None and player.is_alive():
            player.stop()
            if wait:
                player.join(timeout=1.0)
        self.engine.stop()
        if self._state not in (IDLE, STOPPED, ERROR):
            self._state = STOPPED
            self._emit("state", {"state": STOPPED})

    # -- 回调 -------------------------------------------------------------

    def _player_first_audio(self, session: int, timestamp: float) -> None:
        if session != self._session:
            return
        if self._state in (STOPPED, ERROR):
            return
        self._state = SPEAKING
        self._emit(
            "first_audio",
            {
                "elapsed": max(0.0, timestamp - self._started_at),
                "state": SPEAKING,
            },
        )

    def _player_done(self, session: int) -> None:
        if session != self._session:
            return
        if self._state in (STOPPED, ERROR):
            return
        self._state = IDLE
        self._emit("playback_done", {"state": IDLE})

    def _player_error(self, session: int, message: str) -> None:
        if session != self._session:
            return
        self._state = ERROR
        self._emit(ERROR, {"message": message, "state": ERROR})
        self.engine.stop()
        if self._sink is not None:
            self._sink.close()

    def _on_engine_event(self, event: str, payload: dict[str, Any]) -> None:
        if event == FIRST_CHUNK_EVENT:
            self._emit("first_chunk", payload)
            return
        if event == DONE_EVENT:
            # 音频已经全部交给播放器，播放器会在放完后发 playback_done
            return
        if event == ERROR_EVENT:
            if self._state in (STOPPED, ERROR):
                return
            self._state = ERROR
            self._emit(ERROR, payload)
            if self._sink is not None:
                self._sink.close()
            if self._player is not None and self._player.is_alive():
                self._player.stop()
            return
        if event == "engine_status":
            self._emit("engine_status", payload)
