"""Edge 在线语音引擎。

不直接使用 ``edge_tts.Communicate``，而是复用它的协议常量和 DRM 签名，
自己维护一条“常热”WebSocket：

* 程序启动即在后台完成 TLS / WebSocket 握手；
* 每次朗读只发 SSML，省掉约 0.7 秒的建连时间；
* 空闲时用 WebSocket ping 保活，掉线后自动重连；
* 音频以 MP3 分片直接推进播放器，边收边播。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable

import aiohttp

from .textutil import split_for_speech

log = logging.getLogger(__name__)

FIRST_CHUNK_EVENT = "first_chunk"
STATUS_EVENT = "engine_status"
DONE_EVENT = "engine_done"
ERROR_EVENT = "engine_error"

CONNECT_TIMEOUT = 12.0
RECEIVE_TIMEOUT = 35.0
PING_INTERVAL = 20.0


class EdgeEngineError(RuntimeError):
    """带用户可读中文说明的异常。"""


def _friendly(exc: BaseException) -> str:
    if isinstance(exc, EdgeEngineError):
        return str(exc)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "网络超时，请检查网络连接后重试"
    text = str(exc).strip() or exc.__class__.__name__
    if "403" in text:
        return "语音服务拒绝连接（403），请校准系统时间或稍后重试"
    if isinstance(exc, aiohttp.ClientError):
        return "连接微软语音服务失败，请检查网络或代理设置"
    if "1011" in text or "internal" in text.lower():
        return "语音服务暂时不可用，请稍后重试"
    return f"合成失败：{text[:160]}"


def _config_message() -> str:
    from edge_tts.communicate import date_to_string

    return (
        "X-Timestamp:" + date_to_string() + "\r\n"
        "Content-Type:application/json; charset=utf-8\r\n"
        "Path:speech.config\r\n\r\n"
        '{"context":{"synthesis":{"audio":{"metadataoptions":{'
        '"sentenceBoundaryEnabled":"true","wordBoundaryEnabled":"false"},'
        '"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n'
    )


def _ssml_message(
    text: str, voice: str, rate: int, pitch: int, volume: int
) -> str:
    from edge_tts.communicate import connect_id, date_to_string, mkssml, ssml_headers_plus_data
    from edge_tts.data_classes import TTSConfig

    config = TTSConfig(
        voice,
        f"{rate:+d}%",
        f"{volume:+d}%",
        f"{pitch:+d}Hz",
        "SentenceBoundary",
    )
    request_id = connect_id()
    return ssml_headers_plus_data(
        request_id, date_to_string(), mkssml(config, text)
    )


class EdgeEngine:
    """线程安全的 Edge TTS 引擎。

    ``emit(event, payload)`` 会在网络线程或内部循环里被调用，GUI 侧应把事件
    放进队列，再回到主线程处理。
    """

    def __init__(self, emit: Callable[[str, dict[str, Any]], None]) -> None:
        self._emit = emit
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()
        self._shutdown: asyncio.Event | None = None
        self._connector: aiohttp.TCPConnector | None = None
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._connect_lock: asyncio.Lock | None = None
        self._seq_task: asyncio.Task[None] | None = None
        self._maintain_task: asyncio.Task[None] | None = None
        self._turn_active = False
        self._status = ""
        self._last_error = ""

    # -- 生命周期 ---------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._thread_main, name="edge-tts", daemon=True
        )
        self._thread.start()
        self._loop_ready.wait(timeout=3.0)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception:
            log.exception("Edge 网络线程异常退出")
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            loop.close()

    async def _main(self) -> None:
        self._shutdown = asyncio.Event()
        self._connect_lock = asyncio.Lock()
        self._connector = aiohttp.TCPConnector(
            limit=8,
            ttl_dns_cache=600,
            enable_cleanup_closed=True,
        )
        self._session = aiohttp.ClientSession(
            connector=self._connector, trust_env=True
        )
        self._loop_ready.set()
        self._maintain_task = asyncio.create_task(self._maintain())
        try:
            await self._shutdown.wait()
        finally:
            if self._maintain_task:
                self._maintain_task.cancel()
                try:
                    await self._maintain_task
                except BaseException:
                    pass
            await self._destroy_ws()
            if self._session:
                await self._session.close()
            if self._connector:
                await self._connector.close()

    def shutdown(self, timeout: float = 3.0) -> None:
        loop = self._loop
        if loop and loop.is_running() and self._shutdown:
            loop.call_soon_threadsafe(self._shutdown.set)
        if self._thread:
            self._thread.join(timeout=timeout)

    # -- 状态 -------------------------------------------------------------

    def _set_status(self, status: str, message: str = "") -> None:
        if status == self._status and message == self._last_error:
            return
        self._status = status
        self._last_error = message
        self._emit(STATUS_EVENT, {"status": status, "message": message})

    @property
    def status(self) -> str:
        return self._status

    # -- 连接管理 ---------------------------------------------------------

    async def _ensure_ws(self):
        if self._ws is not None and not self._ws.closed:
            return self._ws
        assert self._connect_lock is not None
        async with self._connect_lock:
            if self._ws is not None and not self._ws.closed:
                return self._ws
            await self._destroy_ws()
            self._set_status("connecting")
            last_error: BaseException | None = None
            for attempt in range(2):
                try:
                    ws = await self._connect_once()
                    self._ws = ws
                    self._set_status("ready")
                    return ws
                except aiohttp.WSServerHandshakeError as exc:
                    last_error = exc
                    if exc.status == 403 and attempt == 0:
                        await self._adjust_clock(exc)
                        continue
                    self._set_status("offline", _friendly(exc))
                    raise EdgeEngineError(_friendly(exc)) from exc
                except (asyncio.TimeoutError, TimeoutError) as exc:
                    last_error = exc
                    self._set_status("offline", _friendly(exc))
                    raise EdgeEngineError(_friendly(exc)) from exc
                except aiohttp.ClientError as exc:
                    last_error = exc
                    self._set_status("offline", _friendly(exc))
                    raise EdgeEngineError(_friendly(exc)) from exc
            if last_error:
                message = _friendly(last_error)
                self._set_status("offline", message)
                raise EdgeEngineError(message)
            raise EdgeEngineError("无法连接语音服务")

    async def _connect_once(self):
        from edge_tts.communicate import connect_id
        from edge_tts.constants import SEC_MS_GEC_VERSION, WSS_HEADERS, WSS_URL
        from edge_tts.drm import DRM

        if self._session is None:
            raise EdgeEngineError("语音服务尚未初始化")
        url = (
            f"{WSS_URL}&ConnectionId={connect_id()}"
            f"&Sec-MS-GEC={DRM.generate_sec_ms_gec()}"
            f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}"
        )
        return await asyncio.wait_for(
            self._session.ws_connect(
                url,
                compress=15,
                proxy=None,
                headers=DRM.headers_with_muid(WSS_HEADERS),
                ssl=_ssl_context(),
            ),
            timeout=CONNECT_TIMEOUT,
        )

    async def _adjust_clock(self, exc: BaseException) -> None:
        try:
            from edge_tts.drm import DRM

            DRM.handle_client_response_error(exc)  # type: ignore[arg-type]
            log.info("已根据服务器时间校准语音服务签名")
        except Exception:
            log.debug("校准服务器时间失败", exc_info=True)

    async def _destroy_ws(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def _maintain(self) -> None:
        backoff = 0.0
        while self._shutdown is not None and not self._shutdown.is_set():
            try:
                if self._ws is None or self._ws.closed:
                    if not self._turn_active:
                        await self._ensure_ws()
                    backoff = 0.0
                elif not self._turn_active:
                    await asyncio.wait_for(self._ws.ping(), timeout=8.0)
                await asyncio.sleep(PING_INTERVAL)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._set_status("offline", _friendly(exc))
                await self._destroy_ws()
                backoff = min(30.0, max(2.0, backoff * 1.8 or 2.0))
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise

    # -- 朗读 -------------------------------------------------------------

    def speak(
        self,
        text: str,
        voice: str,
        rate: int,
        pitch: int,
        volume: int,
        sink,
        started_at: float,
    ) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            self._emit(
                ERROR_EVENT,
                {"message": "语音引擎尚未就绪，请稍后重试", "detail": "loop not running"},
            )
            return
        loop.call_soon_threadsafe(
            self._submit, text, voice, rate, pitch, volume, sink, started_at
        )

    def _submit(
        self,
        text: str,
        voice: str,
        rate: int,
        pitch: int,
        volume: int,
        sink,
        started_at: float,
    ) -> None:
        old = self._seq_task
        if old is not None and not old.done():
            old.cancel()
        task = self._loop.create_task(  # type: ignore[union-attr]
            self._chain(old, text, voice, rate, pitch, volume, sink, started_at)
        )
        self._seq_task = task

    async def _chain(self, old, text, voice, rate, pitch, volume, sink, started_at):
        if old is not None:
            try:
                await old
            except BaseException:
                pass
        await self._run_sequence(
            text, voice, rate, pitch, volume, sink, started_at
        )

    async def _run_sequence(
        self,
        text: str,
        voice: str,
        rate: int,
        pitch: int,
        volume: int,
        sink,
        started_at: float,
    ) -> None:
        self._turn_active = True
        first_chunk = True
        try:
            chunks = split_for_speech(text)
            if not chunks:
                raise EdgeEngineError("没有可朗读的文字")
            ws = await self._ensure_ws()
            for index, chunk in enumerate(chunks):
                await self._stream_turn(
                    ws,
                    chunk,
                    voice,
                    rate,
                    pitch,
                    volume,
                    sink,
                    started_at,
                    first_chunk,
                )
                first_chunk = False
                if index < len(chunks) - 1:
                    # 给服务端一点喘息时间，同时让本地播放器保持缓冲
                    await asyncio.sleep(0)
            sink.finish()
            self._emit(DONE_EVENT, {})
        except asyncio.CancelledError:
            sink.close()
            await self._destroy_ws()
            raise
        except Exception as exc:
            sink.close()
            await self._destroy_ws()
            log.warning("朗读失败: %s", exc, exc_info=True)
            self._emit(
                ERROR_EVENT,
                {"message": _friendly(exc), "detail": repr(exc)},
            )
        finally:
            self._turn_active = False

    async def _stream_turn(
        self,
        ws,
        text: str,
        voice: str,
        rate: int,
        pitch: int,
        volume: int,
        sink,
        started_at: float,
        first_chunk: bool,
    ) -> None:
        await ws.send_str(_config_message())
        await ws.send_str(_ssml_message(text, voice, rate, pitch, volume))
        got_audio = False
        while True:
            try:
                message = await asyncio.wait_for(
                    ws.receive(), timeout=RECEIVE_TIMEOUT
                )
            except asyncio.TimeoutError as exc:
                raise EdgeEngineError("等待语音数据超时") from exc

            if message.type == aiohttp.WSMsgType.TEXT:
                data = message.data
                if "Path:turn.end" in data:
                    if not got_audio:
                        raise EdgeEngineError("语音服务没有返回音频，请稍后重试")
                    return
                continue
            if message.type == aiohttp.WSMsgType.BINARY:
                raw = message.data
                if len(raw) < 2:
                    continue
                header_len = int.from_bytes(raw[:2], "big")
                if header_len > len(raw):
                    continue
                header = raw[:header_len]
                if b"Path:audio" not in header:
                    continue
                payload = raw[header_len + 2 :]
                if not payload:
                    continue
                if not got_audio:
                    got_audio = True
                    if first_chunk:
                        self._emit(
                            FIRST_CHUNK_EVENT,
                            {"elapsed": time.perf_counter() - started_at},
                        )
                sink.feed(payload)
                continue
            if message.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
            ):
                raise EdgeEngineError("语音连接被服务器关闭")
            if message.type == aiohttp.WSMsgType.ERROR:
                raise EdgeEngineError("语音连接异常")

    def stop(self) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(self._cancel_current)

    def _cancel_current(self) -> None:
        task = self._seq_task
        if task is not None and not task.done():
            task.cancel()


def _ssl_context():
    from edge_tts.communicate import _SSL_CTX

    return _SSL_CTX
