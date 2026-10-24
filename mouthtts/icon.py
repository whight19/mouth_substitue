"""程序图标：运行时生成一个简单的 .ico（不依赖 Pillow）。"""

from __future__ import annotations

import struct
from pathlib import Path


def _pixel(x: int, y: int) -> tuple[int, int, int, int]:
    # 底色 #8093ff
    r, g, b, a = 0x80, 0x93, 0xFF, 255
    # 四根竖向波形条，颜色 #0d1017
    bars = ((7, 11), (13, 20), (19, 15), (25, 8))
    for left, height in bars:
        top = (32 - height) // 2
        if left <= x < left + 3 and top <= y < top + height:
            return 0x0D, 0x10, 0x17, 255
    return r, g, b, a


def ensure_icon(path: Path) -> Path | None:
    """生成 32x32 图标；失败时返回 None，不影响程序启动。"""
    try:
        if path.exists() and path.stat().st_size > 100:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        width = height = 32
        pixels = bytearray()
        # DIB 像素从下往上存
        for y in range(height - 1, -1, -1):
            for x in range(width):
                r, g, b, a = _pixel(x, y)
                pixels.extend((b, g, r, a))
        mask = b"\x00" * (width * height // 8)
        header = struct.pack(
            "<IiiHHIIiiII",
            40,
            width,
            height * 2,
            1,
            32,
            0,
            width * height * 4,
            0,
            0,
            0,
            0,
        )
        image = header + bytes(pixels) + mask
        entry = struct.pack(
            "<BBBBHHII", width, height, 0, 0, 1, 32, len(image), 22
        )
        data = struct.pack("<HHH", 0, 1, 1) + entry + image
        path.write_bytes(data)
        return path
    except Exception:
        return None
