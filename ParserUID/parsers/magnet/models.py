"""磁力链接接口数据模型。"""

from __future__ import annotations

from msgspec import Struct


class ScreenshotItem(Struct):
    screenshot: str = ""


class MagnetInfoResponse(Struct):
    name: str = "未知资源"
    file_type: str = "未知"
    size: float | int = 0
    screenshots: list[ScreenshotItem] = []
    error: str = ""
