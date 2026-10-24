"""嘴替 MouthSubstitute 启动入口。

图形界面：

    python app.py

也可以不开界面，直接做一次合成自测：

    python app.py --selftest --device "CABLE Input"
    python app.py --list-devices
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import queue
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Any

log = logging.getLogger("mouthtts")


def _set_dpi_awareness() -> None:
    """避免 Windows 缩放时界面发虚。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def setup_logging(verbose: bool = False) -> None:
    from mouthtts import config

    config.ensure_dirs()
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    file_handler = RotatingFileHandler(
        config.LOG_DIR / "app.log",
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        console.setLevel(level)
        root.addHandler(console)


def _match_device(text: str):
    from mouthtts import audio

    devices = audio.list_output_devices()
    if not text:
        return audio.default_or_first()
    needle = text.strip().lower()
    for device in devices:
        if needle in device.name.lower() or needle in device.key.lower():
            return device
    return None


def _cmd_list_devices() -> int:
    from mouthtts import audio

    devices = audio.list_output_devices()
    if not devices:
        print("未发现可播放设备")
        return 1
    print("可播放设备：")
    for device in devices:
        print(f"  [{device.api_short:10s}] {device.name}")
        print(f"      index={device.index}  {device.samplerate} Hz  {device.channels} ch")
    default = audio.default_or_first()
    if default:
        print(f"\n系统默认：{default.name} [{default.api_short}]")
    return 0


def _cmd_list_voices() -> int:
    from mouthtts import voices

    cached = voices.load_cached_voices()
    for voice in cached:
        short = str(voice.get("ShortName", ""))
        print(
            f"{short:40s} {voice.get('Locale', ''):8s} "
            f"{voice.get('Gender', ''):7s} {voices.voice_tags(voice)}"
        )
    print(f"\n共 {len(cached)} 个音色（缓存或内置中文列表）")
    return 0


def _cmd_selftest(args: argparse.Namespace) -> int:
    from mouthtts import audio, config
    from mouthtts.pipeline import TTSPipeline

    device = _match_device(args.device)
    if device is None:
        print(f"找不到输出设备：{args.device!r}")
        print(audio.describe_devices())
        return 2
    text = (args.text or "你好，这是一次语音合成自测。").strip()
    cfg = config.load()
    voice = args.voice or cfg["voice"]
    print(f"设备：{device.name} [{device.api_short}] {device.samplerate} Hz")
    print(f"音色：{voice}")
    print(f"文本：{text}")

    events: queue.Queue[tuple[float, str, dict[str, Any]]] = queue.Queue()

    def emit(kind: str, payload: dict[str, Any]) -> None:
        events.put((time.perf_counter(), kind, dict(payload)))

    pipeline = TTSPipeline(emit)
    pipeline.start()
    print("连接语音服务…")
    ready = False
    deadline = time.time() + 15
    while time.time() < deadline and not ready:
        try:
            _ts, kind, payload = events.get(timeout=0.2)
        except queue.Empty:
            continue
        if kind == "engine_status":
            if payload.get("status") == "ready":
                ready = True
            elif payload.get("status") == "offline":
                print(f"连接失败：{payload.get('message')}")
                break
        elif kind == "error":
            print(f"错误：{payload.get('message')}")
            pipeline.shutdown()
            return 3
    if not ready:
        print("语音服务未在 15 秒内就绪，仍尝试朗读…")

    while not events.empty():
        events.get()

    print("开始合成…")
    t0 = time.perf_counter()
    pipeline.speak(
        text,
        voice,
        int(args.rate),
        int(args.pitch),
        int(args.volume),
        device,
        prebuffer_ms=60,
    )
    first_audio: float | None = None
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            _ts, kind, payload = events.get(timeout=0.2)
        except queue.Empty:
            continue
        if kind == "first_chunk":
            print(f"首包：{payload.get('elapsed', 0) * 1000:.0f} ms")
        elif kind == "first_audio":
            first_audio = float(payload.get("elapsed", 0))
            print(f"首音：{first_audio * 1000:.0f} ms")
        elif kind == "playback_done":
            print(f"播放完成：{time.perf_counter() - t0:.2f} s")
            pipeline.shutdown()
            return 0
        elif kind == "error":
            print(f"错误：{payload.get('message')}")
            pipeline.shutdown()
            return 3
    print("超时未完成")
    pipeline.shutdown()
    return 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mouthtts",
        description="嘴替 —— 低延迟文字转语音，输出到指定声卡",
    )
    parser.add_argument("--version", action="store_true", help="显示版本")
    parser.add_argument("--list-devices", action="store_true", help="列出输出设备")
    parser.add_argument("--list-voices", action="store_true", help="列出音色")
    parser.add_argument("--selftest", action="store_true", help="无界面合成自测")
    parser.add_argument("--device", default="", help="输出设备名的一部分")
    parser.add_argument("--voice", default="", help="音色 ID")
    parser.add_argument("--text", default="", help="自测文本")
    parser.add_argument("--rate", type=int, default=0, help="语速 -50..100")
    parser.add_argument("--pitch", type=int, default=0, help="音调 -50..50")
    parser.add_argument("--volume", type=int, default=0, help="音量 -50..50")
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _set_dpi_awareness()
    setup_logging(args.verbose)

    from mouthtts import __version__

    if args.version:
        print(f"嘴替 MouthSubstitute {__version__}")
        return 0
    if args.list_devices:
        return _cmd_list_devices()
    if args.list_voices:
        return _cmd_list_voices()
    if args.selftest:
        return _cmd_selftest(args)

    try:
        from mouthtts.ui import run

        run()
    except Exception:
        log.exception("界面启动失败")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
