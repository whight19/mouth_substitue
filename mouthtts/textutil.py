"""文本清洗与分句。

Edge 的单次请求有长度上限，同时为了让“停止”更跟手，长文本会按句子切成
若干段依次合成；短文本保持整段，语气最自然。
"""

from __future__ import annotations

import re

MAX_CHUNK_CHARS = 1200
MAX_TEXT_CHARS = 20000

_SENTENCE_END = "。！？!?…；;"
_SOFT_BREAKS = "，,、.．:：— \n\t"
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)


def clean(text: str) -> str:
    """去掉服务端不接受的不可见字符，统一换行与空白。"""
    text = (text or "").translate(_ZERO_WIDTH)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Edge 接口不接受大部分 C0 控制字符
    text = "".join(
        ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32
    )
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def char_count(text: str) -> int:
    """界面上显示的“字数”，不计空白。"""
    return len(re.sub(r"\s", "", text or ""))


def split_sentences(text: str) -> list[str]:
    """按中英文句末标点和换行切句，标点跟随前句。"""
    parts: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in _SENTENCE_END or ch == "\n":
            piece = "".join(buf).strip()
            if piece:
                parts.append(piece)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _hard_split(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = -1
        for mark in _SOFT_BREAKS:
            cut = max(cut, rest.rfind(mark, 0, limit))
        if cut < limit // 3:
            cut = limit
        else:
            cut += 1
        piece = rest[:cut].strip()
        if piece:
            chunks.append(piece)
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return chunks


def split_for_speech(
    text: str, max_chars: int = MAX_CHUNK_CHARS
) -> list[str]:
    """合并短句、拆分超长句，返回适合逐段合成的小段。"""
    text = clean(text)[:MAX_TEXT_CHARS]
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(sentence, max_chars))
            continue
        if len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return [c for c in (chunk.strip() for chunk in chunks) if c]
