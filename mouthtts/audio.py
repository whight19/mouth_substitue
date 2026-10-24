"""音频输出设备与低延迟播放。

播放走 PortAudio 的 WASAPI 共享模式（sounddevice），采样格式固定为
16-bit 立体声；解码由 miniaudio 完成，并自动把 24 kHz 的合成结果重采样到
设备采样率。
"""

from __future__ import annotations

import math
import threading
from array import array
from dataclasses import dataclass
from typing import Any

try:
    import sounddevice as sd
except Exception as exc:  # pragma: no cover - 仅在音频库缺失时触发
    sd = None  # type: ignore[assignment]
    _SD_ERROR = exc
else:
    _SD_ERROR = None

INCLUDED_HOSTAPIS = {
    "Windows WASAPI",
    "MME",
    "Windows DirectSound",
}
HOSTAPI_SHORT = {
    "Windows WASAPI": "WASAPI",
    "MME": "MME",
    "Windows DirectSound": "DirectSound",
    "Windows WDM-KS": "WDM-KS",
}
EXCLUDED_NAMES = {
    "Microsoft 声音映射器 - Output",
    "Microsoft Sound Mapper - Output",
    "主声音驱动程序",
    "Primary Sound Driver",
}


def audio_available() -> bool:
    return sd is not None


def audio_error() -> str:
    return "" if _SD_ERROR is None else str(_SD_ERROR)


@dataclass(frozen=True)
class OutputDevice:
    index: int
    name: str
    hostapi: str
    channels: int
    samplerate: int

    @property
    def api_short(self) -> str:
        return HOSTAPI_SHORT.get(self.hostapi, self.hostapi)

    @property
    def key(self) -> str:
        return f"{self.hostapi}|{self.name}"

    @property
    def label(self) -> str:
        return f"{self.name}   [{self.api_short}]"

    def to_config(self) -> dict[str, str]:
        return {"hostapi": self.hostapi, "name": self.name}


def _hostapi_name(hostapis: list[dict[str, Any]], index: int) -> str:
    try:
        return str(hostapis[index]["name"])
    except (IndexError, KeyError, TypeError):
        return ""


def list_output_devices() -> list[OutputDevice]:
    """列出可播放的设备；同名设备按主机 API 去重。"""
    if sd is None:
        return []
    try:
        hostapis = list(sd.query_hostapis())
        devices = list(sd.query_devices())
    except Exception:
        return []

    result: list[OutputDevice] = []
    seen: set[str] = set()
    for index, info in enumerate(devices):
        try:
            if int(info["max_output_channels"]) <= 0:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        name = str(info.get("name") or "").strip()
        if not name or name in EXCLUDED_NAMES:
            continue
        api = _hostapi_name(hostapis, int(info.get("hostapi", 0)))
        if api not in INCLUDED_HOSTAPIS:
            continue
        key = f"{api}|{name}"
        if key in seen:
            continue
        seen.add(key)
        try:
            samplerate = int(round(float(info.get("default_samplerate") or 48000)))
        except (TypeError, ValueError):
            samplerate = 48000
        if samplerate < 8000:
            samplerate = 48000
        channels = min(2, max(1, int(info.get("max_output_channels") or 2)))
        result.append(
            OutputDevice(
                index=index,
                name=name,
                hostapi=api,
                channels=channels,
                samplerate=samplerate,
            )
        )

    result.sort(key=lambda d: (0 if d.hostapi == "Windows WASAPI" else 1, d.name.lower()))
    return result


def system_default_device() -> OutputDevice | None:
    if sd is None:
        return None
    try:
        info = sd.query_devices(kind="output")
    except Exception:
        return None
    try:
        index = int(info["index"])
        name = str(info.get("name") or "").strip()
        hostapis = list(sd.query_hostapis())
        api = _hostapi_name(hostapis, int(info.get("hostapi", 0)))
        sr = int(round(float(info.get("default_samplerate") or 48000)))
        channels = min(2, max(1, int(info.get("max_output_channels") or 2)))
    except (KeyError, TypeError, ValueError):
        return None
    if not name or api not in INCLUDED_HOSTAPIS:
        return None
    return OutputDevice(index, name, api, channels, sr)


def find_device(key: str | None) -> OutputDevice | None:
    if not key:
        return None
    for device in list_output_devices():
        if device.key == key:
            return device
    return None


def resolve_device(device_cfg: dict[str, Any] | None) -> OutputDevice | None:
    """优先匹配保存的设备；找不到就退回系统默认设备。"""
    if isinstance(device_cfg, dict) and device_cfg.get("name"):
        key = f"{device_cfg.get('hostapi', '')}|{device_cfg['name']}"
        found = find_device(key)
        if found:
            return found
    return system_default_device()


def default_or_first() -> OutputDevice | None:
    """系统默认设备不可用时，退回设备列表里的第一个。"""
    device = system_default_device()
    if device is not None:
        return device
    devices = list_output_devices()
    return devices[0] if devices else None


def open_stream(
    device: OutputDevice,
    samplerate: int | None = None,
    channels: int | None = None,
):
    if sd is None:
        raise RuntimeError(f"音频库不可用：{_SD_ERROR}")
    sr = int(samplerate or device.samplerate)
    ch = min(int(channels or device.channels), device.channels) or 1
    return sd.RawOutputStream(
        device=device.index,
        samplerate=sr,
        channels=ch,
        dtype="int16",
        latency="low",
    )


def play_test_tone(
    device: OutputDevice,
    duration: float = 0.32,
    frequency: float = 660.0,
    volume: float = 0.10,
    stop_event: threading.Event | None = None,
) -> None:
    """播放一段带淡入淡出的短音，用来确认声音确实进了目标设备。"""
    sr = device.samplerate
    ch = device.channels
    total = max(1, int(sr * duration))
    fade = max(1, int(sr * 0.012))
    samples = array("h")
    for i in range(total):
        gain = 1.0
        if i < fade:
            gain = i / fade
        elif i > total - fade:
            gain = max(0.0, (total - i) / fade)
        value = int(32767 * volume * gain * math.sin(2 * math.pi * frequency * i / sr))
        for _ in range(ch):
            samples.append(value)
    stream = open_stream(device, samplerate=sr, channels=ch)
    try:
        stream.start()
        if stop_event is None or not stop_event.is_set():
            stream.write(samples.tobytes())
    finally:
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass


def describe_devices() -> str:
    devices = list_output_devices()
    if not devices:
        return "未发现可用输出设备"
    return "\n".join(f"{d.label}  ({d.samplerate} Hz, {d.channels} ch)" for d in devices)
