"""音色目录、音色缓存与内置音色预设。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from . import config

# 短名 -> (中文名, 一句话特点, 性别, 语言, 适用场景)
CURATED_VOICES: list[tuple[str, str, str, str, str, str]] = [
    ("zh-CN-XiaoxiaoNeural", "晓晓", "温柔自然", "女", "普通话", "日常 / 新闻 / 小说"),
    ("zh-CN-XiaoyiNeural", "晓伊", "活泼少女", "女", "普通话", "动画 / 小说"),
    ("zh-CN-YunxiNeural", "云希", "阳光青年", "男", "普通话", "小说 / 对话"),
    ("zh-CN-YunjianNeural", "云健", "激昂解说", "男", "普通话", "体育 / 旁白"),
    ("zh-CN-YunxiaNeural", "云夏", "可爱少年", "男", "普通话", "动画 / 小说"),
    ("zh-CN-YunyangNeural", "云扬", "沉稳播报", "男", "普通话", "新闻 / 播报"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北", "幽默东北话", "女", "东北官话", "方言 / 搞笑"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮", "明亮陕西话", "女", "中原官话", "方言 / 活泼"),
    ("zh-HK-HiuGaaiNeural", "曉佳", "亲切粤语", "女", "粤语", "通用"),
    ("zh-HK-HiuMaanNeural", "曉曼", "温柔粤语", "女", "粤语", "通用"),
    ("zh-HK-WanLungNeural", "雲龍", "稳重粤语", "男", "粤语", "通用"),
    ("zh-TW-HsiaoChenNeural", "曉臻", "亲切台湾腔", "女", "台湾国语", "通用"),
    ("zh-TW-HsiaoYuNeural", "曉雨", "甜美台湾腔", "女", "台湾国语", "通用"),
    ("zh-TW-YunJheNeural", "雲哲", "温和台湾腔", "男", "台湾国语", "通用"),
]

CURATED_MAP = {item[0]: item for item in CURATED_VOICES}

LOCALE_LABELS = {
    "zh-CN": "普通话",
    "zh-CN-liaoning": "东北官话",
    "zh-CN-shaanxi": "中原官话",
    "zh-HK": "粤语",
    "zh-TW": "台湾国语",
    "en-US": "英语（美国）",
    "en-GB": "英语（英国）",
    "en-AU": "英语（澳洲）",
    "ja-JP": "日语",
    "ko-KR": "韩语",
    "fr-FR": "法语",
    "de-DE": "德语",
    "es-ES": "西班牙语",
    "ru-RU": "俄语",
}

GENDER_LABELS = {
    "Female": "女",
    "Male": "男",
    "Neutral": "中性",
}

TAG_LABELS = {
    "Warm": "温暖",
    "News": "新闻",
    "Novel": "小说",
    "Lively": "活泼",
    "Passion": "激情",
    "Sports": "体育",
    "Cartoon": "动画",
    "Cute": "可爱",
    "Professional": "专业",
    "Reliable": "可靠",
    "Friendly": "亲切",
    "Positive": "积极",
    "Humorous": "幽默",
    "Dialect": "方言",
    "Bright": "明亮",
    "General": "通用",
    "Conversation": "对话",
    "Copilot": "助手",
    "Expressive": "表现力",
    "Caring": "体贴",
    "Pleasant": "悦耳",
    "Cheerful": "开朗",
    "Clear": "清晰",
    "Conversational": "口语",
    "Approachable": "亲和",
    "Casual": "随性",
    "Sincere": "真诚",
    "Confident": "自信",
    "Authentic": "真实",
    "Honest": "诚恳",
    "Sunshine": "阳光",
    "Emotional": "情感",
    "Story": "故事",
    "Advertisement": "广告",
}


# 断网 / 首次启动时仍可用的中文音色
FALLBACK_VOICES: list[dict[str, Any]] = [
    {
        "ShortName": short,
        "Gender": gender,
        "Locale": "zh-CN" if locale in ("普通话", "东北官话", "中原官话") else
        ("zh-HK" if locale == "粤语" else "zh-TW"),
        "FriendlyName": f"{alias} - {desc}",
        "VoiceTag": {"ContentCategories": [scene], "VoicePersonalities": [desc]},
        "_curated": True,
    }
    for short, alias, desc, gender, locale, scene in CURATED_VOICES
]


def curated_display(short_name: str) -> str:
    item = CURATED_MAP.get(short_name)
    if item:
        return f"{item[1]} · {item[2]}（{item[4]}）"
    return short_name


def curated_options() -> list[tuple[str, str]]:
    """返回 [(显示文本, 音色短名)]，供下拉框使用。"""
    return [(curated_display(item[0]), item[0]) for item in CURATED_VOICES]


def locale_label(locale: str) -> str:
    if locale in LOCALE_LABELS:
        return LOCALE_LABELS[locale]
    if locale.startswith("zh"):
        return "中文"
    return locale


def gender_label(gender: str) -> str:
    return GENDER_LABELS.get(gender, gender)


def voice_tags(voice: dict[str, Any]) -> str:
    tag = voice.get("VoiceTag") or {}
    values: list[str] = []
    for key in ("VoicePersonalities", "ContentCategories", "StyleList"):
        raw = tag.get(key)
        if isinstance(raw, list):
            values.extend(str(v) for v in raw if v)
    # 去重且保持顺序
    seen: set[str] = set()
    result = []
    for value in values:
        value = value.strip().strip("'\"")
        if value and value not in seen:
            seen.add(value)
            result.append(TAG_LABELS.get(value, value))
    return " / ".join(result[:4])


def display_name(voice: dict[str, Any]) -> str:
    short = str(voice.get("ShortName", ""))
    item = CURATED_MAP.get(short)
    if item:
        return f"{item[1]} · {item[2]}"
    friendly = str(voice.get("FriendlyName") or short)
    # FriendlyName 形如 "Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)"
    friendly = friendly.replace("Microsoft ", "").split(" - ")[0].strip()
    return friendly or short


def sort_key(voice: dict[str, Any]) -> tuple[int, int, str]:
    short = str(voice.get("ShortName", ""))
    if short in CURATED_MAP:
        return (0, [v[0] for v in CURATED_VOICES].index(short), short)
    locale = str(voice.get("Locale", ""))
    return (1 if locale.startswith("zh") else 2, 0, short)


async def _list_voices(timeout: float) -> list[dict[str, Any]]:
    import edge_tts

    return await asyncio.wait_for(edge_tts.list_voices(), timeout=timeout)


def fetch_voices(timeout: float = 20.0) -> list[dict[str, Any]]:
    """在线获取全部音色；失败时抛异常，由调用方决定是否使用缓存。"""
    return asyncio.run(_list_voices(timeout))


def load_cached_voices() -> list[dict[str, Any]]:
    cached = config.load_voice_cache()
    if cached:
        return sorted(cached, key=sort_key)
    return list(FALLBACK_VOICES)


def merge_curated(voices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保证缓存的旧列表也包含内置中文音色。"""
    known = {str(v.get("ShortName")) for v in voices}
    result = list(voices)
    for item in FALLBACK_VOICES:
        if item["ShortName"] not in known:
            result.append(item)
    return sorted(result, key=sort_key)


# ---------------------------------------------------------------------------
# 内置音色预设（音色 + 语调，就是“自定义音色”的起点）
# ---------------------------------------------------------------------------

BUILTIN_PRESETS: list[dict[str, Any]] = [
    {
        "name": "温柔女声 · 晓晓",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": -3,
        "pitch": -4,
        "volume": 0,
    },
    {
        "name": "甜美女声 · 晓伊",
        "voice": "zh-CN-XiaoyiNeural",
        "rate": 2,
        "pitch": 8,
        "volume": 0,
    },
    {
        "name": "阳光少年 · 云希",
        "voice": "zh-CN-YunxiNeural",
        "rate": 3,
        "pitch": 2,
        "volume": 0,
    },
    {
        "name": "激情解说 · 云健",
        "voice": "zh-CN-YunjianNeural",
        "rate": 8,
        "pitch": -6,
        "volume": 0,
    },
    {
        "name": "可爱少年 · 云夏",
        "voice": "zh-CN-YunxiaNeural",
        "rate": 5,
        "pitch": 10,
        "volume": 0,
    },
    {
        "name": "新闻播报 · 云扬",
        "voice": "zh-CN-YunyangNeural",
        "rate": -2,
        "pitch": -2,
        "volume": 0,
    },
    {
        "name": "东北搞笑 · 晓北",
        "voice": "zh-CN-liaoning-XiaobeiNeural",
        "rate": 5,
        "pitch": 4,
        "volume": 0,
    },
    {
        "name": "粤语温柔 · 曉曼",
        "voice": "zh-HK-HiuMaanNeural",
        "rate": -2,
        "pitch": 0,
        "volume": 0,
    },
    {
        "name": "台湾甜妹 · 曉雨",
        "voice": "zh-TW-HsiaoYuNeural",
        "rate": 0,
        "pitch": 6,
        "volume": 0,
    },
]


def preset_names(user_presets: list[dict[str, Any]]) -> list[str]:
    return [p["name"] for p in BUILTIN_PRESETS] + [
        p["name"] for p in user_presets if p.get("name")
    ]


def find_preset(
    name: str, user_presets: list[dict[str, Any]]
) -> dict[str, Any] | None:
    for preset in BUILTIN_PRESETS:
        if preset["name"] == name:
            return preset
    for preset in user_presets:
        if preset.get("name") == name:
            return preset
    return None


def is_builtin(name: str) -> bool:
    return any(p["name"] == name for p in BUILTIN_PRESETS)


def cache_age_text(ts: float) -> str:
    if not ts:
        return "未缓存"
    delta = max(0, time.time() - ts)
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    return f"{int(delta // 86400)} 天前"
