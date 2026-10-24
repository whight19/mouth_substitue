"""配置与持久化。

配置放在程序目录的 data/config.json，绿色免安装：整个文件夹拷走即可，
不会写注册表，也不会写到用户目录。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable

APP_DIR_NAME = "MouthSubstitute"

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "voice": "zh-CN-XiaoxiaoNeural",
    "rate": 0,
    "pitch": 0,
    "volume": 0,
    "prebuffer_ms": 60,
    "device": None,  # {"hostapi": "Windows WASAPI", "name": "CABLE Input (...)"}
    "geometry": "1120x780",
    "text": "",
    "presets": [],
    "voice_cache_ts": 0,
}

RATE_MIN, RATE_MAX = -50, 100
PITCH_MIN, PITCH_MAX = -50, 50
VOLUME_MIN, VOLUME_MAX = -50, 50
PREBUFFER_MIN, PREBUFFER_MAX = 0, 300


def app_dir() -> Path:
    """程序根目录（源码运行或打包后都指向程序所在文件夹）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _resolve_data_dir() -> Path:
    """优先使用程序目录下的 data/；不可写时退回用户本地目录。"""
    preferred = app_dir() / "data"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return preferred
    except OSError:
        fallback = Path(
            os.environ.get("LOCALAPPDATA") or Path.home()
        ) / "MouthSubstitute"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


DATA_DIR = _resolve_data_dir()
LOG_DIR = DATA_DIR / "logs"
CONFIG_PATH = DATA_DIR / "config.json"
VOICE_CACHE_PATH = DATA_DIR / "voices.json"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _clamp(value: Any, low: int, high: int, fallback: int = 0) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def normalize(cfg: dict[str, Any]) -> dict[str, Any]:
    """把任意读入的配置修正成可用的形态。"""
    result = dict(DEFAULTS)
    if isinstance(cfg, dict):
        result.update(cfg)

    result["voice"] = str(result.get("voice") or DEFAULTS["voice"])
    result["rate"] = _clamp(result.get("rate"), RATE_MIN, RATE_MAX)
    result["pitch"] = _clamp(result.get("pitch"), PITCH_MIN, PITCH_MAX)
    result["volume"] = _clamp(result.get("volume"), VOLUME_MIN, VOLUME_MAX)
    result["prebuffer_ms"] = _clamp(
        result.get("prebuffer_ms"),
        PREBUFFER_MIN,
        PREBUFFER_MAX,
        DEFAULTS["prebuffer_ms"],
    )
    result["text"] = str(result.get("text") or "")[:20000]
    result["geometry"] = str(result.get("geometry") or DEFAULTS["geometry"])
    try:
        result["voice_cache_ts"] = float(result.get("voice_cache_ts") or 0)
    except (TypeError, ValueError):
        result["voice_cache_ts"] = 0.0

    device = result.get("device")
    if isinstance(device, dict) and device.get("name"):
        result["device"] = {
            "hostapi": str(device.get("hostapi") or ""),
            "name": str(device["name"]),
        }
    else:
        result["device"] = None

    presets: list[dict[str, Any]] = []
    for item in result.get("presets") or []:
        if not isinstance(item, dict) or not item.get("name") or not item.get("voice"):
            continue
        presets.append(
            {
                "name": str(item["name"])[:40],
                "voice": str(item["voice"]),
                "rate": _clamp(item.get("rate"), RATE_MIN, RATE_MAX),
                "pitch": _clamp(item.get("pitch"), PITCH_MIN, PITCH_MAX),
                "volume": _clamp(item.get("volume"), VOLUME_MIN, VOLUME_MAX),
            }
        )
    result["presets"] = presets[:60]
    return result


def load() -> dict[str, Any]:
    ensure_dirs()
    cfg: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            # 配置损坏时保留一份现场，避免用户设置直接丢失
            try:
                broken = CONFIG_PATH.with_name(
                    f"config.broken-{time.strftime('%Y%m%d-%H%M%S')}.json"
                )
                shutil.copyfile(CONFIG_PATH, broken)
            except OSError:
                pass
            cfg = {}
    return normalize(cfg)


def save(cfg: dict[str, Any]) -> None:
    ensure_dirs()
    payload = normalize(cfg)
    tmp = CONFIG_PATH.with_name("config.json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, CONFIG_PATH)


def update(**changes: Any) -> dict[str, Any]:
    cfg = load()
    cfg.update(changes)
    save(cfg)
    return normalize(cfg)


# ---------------------------------------------------------------------------
# 音色缓存
# ---------------------------------------------------------------------------


def load_voice_cache() -> list[dict[str, Any]]:
    ensure_dirs()
    try:
        data = json.loads(VOICE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        voices = data.get("voices")
        if isinstance(voices, list):
            return [v for v in voices if isinstance(v, dict) and v.get("ShortName")]
    if isinstance(data, list):
        return [v for v in data if isinstance(v, dict) and v.get("ShortName")]
    return []


def save_voice_cache(voices: Iterable[dict[str, Any]]) -> None:
    ensure_dirs()
    items = [v for v in voices if isinstance(v, dict) and v.get("ShortName")]
    payload = {"ts": time.time(), "voices": items}
    tmp = VOICE_CACHE_PATH.with_name("voices.json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    os.replace(tmp, VOICE_CACHE_PATH)
