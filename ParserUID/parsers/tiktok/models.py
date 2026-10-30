"""TikTok 数据模型定义。"""

from __future__ import annotations

from msgspec import Struct


class TikTokFormat(Struct):
    vcodec: str | None = None


class TikTokThumbnail(Struct):
    url: str = ""


class TikTokInfo(Struct):
    title: str = ""
    description: str = ""
    uploader: str = ""
    channel: str = ""
    duration: float = 0.0
    timestamp: int | None = None
    webpage_url: str = ""
    thumbnail: str = ""
    thumbnails: list[TikTokThumbnail] = []
    formats: list[TikTokFormat] = []
