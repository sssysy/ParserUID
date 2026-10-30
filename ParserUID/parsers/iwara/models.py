"""Iwara 实体与接口数据模型。"""

from __future__ import annotations

from typing import Final
from datetime import datetime
from urllib.parse import quote, urlparse
from collections.abc import Sequence

from msgspec import Struct

from ...utils.exceptions import ParseException

IMAGE_HOST: Final = "i.iwara.tv"
DEFAULT_AVATAR_URL: Final = "https://www.iwara.tv/images/default-avatar.jpg"
QUALITY_FALLBACK: Final = ("Source", "preview", "540", "360")


class IwaraFileRaw(Struct, frozen=True):
    id: str = ""
    name: str = ""
    duration: float = 0.0


class IwaraAvatarRaw(Struct, frozen=True):
    id: str = ""
    name: str = ""


class IwaraUserRaw(Struct, frozen=True):
    name: str = "未知作者"
    username: str = ""
    avatar: IwaraAvatarRaw | None = None


class IwaraTagRaw(Struct, frozen=True):
    id: str = ""


class IwaraVideoRaw(Struct):
    id: str = ""
    title: str = "未知标题"
    body: str | None = None
    rating: str = "general"
    fileUrl: str = ""
    file: IwaraFileRaw = IwaraFileRaw()
    user: IwaraUserRaw = IwaraUserRaw()
    tags: list[IwaraTagRaw] = []
    thumbnail: int = 0
    createdAt: str = ""


class IwaraImageRaw(Struct):
    id: str = ""
    title: str = "未知标题"
    body: str | None = None
    rating: str = "general"
    files: list[IwaraFileRaw] = []
    user: IwaraUserRaw = IwaraUserRaw()
    tags: list[IwaraTagRaw] = []
    thumbnail: IwaraFileRaw | None = None
    createdAt: str = ""


class VideoQualitySrc(Struct, frozen=True):
    download: str = ""


class VideoQualityItem(Struct):
    name: str = ""
    src: VideoQualitySrc = VideoQualitySrc()


class VideoFilesResponse(Struct):
    results: list[VideoQualityItem] = []


def avatar_url(user: IwaraUserRaw) -> str:
    if user.avatar is None or not user.avatar.id or not user.avatar.name:
        return DEFAULT_AVATAR_URL
    return f"https://{IMAGE_HOST}/image/avatar/{quote(user.avatar.id, safe='')}/{quote(user.avatar.name, safe='')}"


def parse_timestamp(raw: str) -> int:
    if not raw:
        return 0
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def video_thumbnail_url(file: IwaraFileRaw, thumbnail: int) -> str | None:
    if not file.id:
        return None
    number = str(thumbnail).zfill(2)
    return f"https://{IMAGE_HOST}/image/thumbnail/{quote(file.id, safe='')}/thumbnail-{number}.jpg"


def image_thumbnail_url(raw: IwaraFileRaw | None) -> str | None:
    if raw is None or not raw.id or not raw.name:
        return None
    return f"https://{IMAGE_HOST}/image/large/{quote(raw.id, safe='')}/{quote(raw.name, safe='')}"


def image_urls(file: IwaraFileRaw, *, large: bool) -> str:
    size = "large" if large else "original"
    return f"https://{IMAGE_HOST}/image/{size}/{quote(file.id, safe='')}/{quote(file.name, safe='')}"


def choose_quality_url(results: Sequence[VideoQualityItem], quality: str) -> tuple[str, str]:
    available: dict[str, str] = {}
    for item in results:
        download = item.src.download
        if not download:
            continue
        url = download if download.startswith("http") else f"https:{download}"
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            continue
        available[item.name] = url

    start = QUALITY_FALLBACK.index(quality) if quality in QUALITY_FALLBACK else 0
    for name in QUALITY_FALLBACK[start:]:
        if name in available:
            return available[name], name
    raise ParseException("未找到可用的 Iwara 视频下载链接")


def is_iwara_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "iwara.tv" or host.endswith(".iwara.tv"))
