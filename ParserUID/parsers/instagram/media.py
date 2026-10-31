"""Instagram 多媒体数据结构与提取工具。"""

from __future__ import annotations

from typing import Literal
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class InstagramTarget:
    url: str
    shortcode: str
    kind: str


@dataclass(frozen=True, slots=True)
class InstagramMedia:
    kind: Literal["image", "video"]
    url: str
    cover: str | None = None
    duration: float = 0.0


@dataclass(frozen=True, slots=True)
class InstagramPost:
    target: InstagramTarget
    title: str | None
    author: str | None
    author_id: str | None
    author_avatar: str | None
    text: str | None
    timestamp: int | None
    cover: str | None
    medias: tuple[InstagramMedia, ...]

    @property
    def is_video_post(self) -> bool:
        """只有全部条目都是视频时才使用视频卡片版式。"""
        return bool(self.medias) and all(item.kind == "video" for item in self.medias)


def parse_target(matched: str) -> InstagramTarget:
    raw = matched.strip()
    if not raw.lower().startswith(("http://", "https://")):
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host not in {"instagram.com", "www.instagram.com", "instagr.am"}:
        raise ValueError("非 Instagram 域名")
    if len(parts) < 2 or parts[0] not in {"p", "reel", "reels", "tv", "share"}:
        raise ValueError("Instagram 链接缺少作品路径")
    kind = parts[0]
    shortcode_index = 1
    if kind == "share":
        if len(parts) >= 3 and parts[1] in {"p", "reel", "reels", "tv"}:
            kind = "reel" if parts[1] == "reels" else parts[1]
            shortcode_index = 2
    shortcode = parts[shortcode_index].strip()
    if not shortcode or not all(char.isascii() and (char.isalnum() or char in "_-") for char in shortcode):
        raise ValueError("Instagram 链接缺少有效作品 ID")
    canonical_kind = "reel" if kind == "reels" else kind
    return InstagramTarget(
        url=f"https://www.instagram.com/{canonical_kind}/{shortcode}/",
        shortcode=shortcode,
        kind=canonical_kind,
    )


def unwrap_entries(info: dict[str, object]) -> list[dict[str, object]]:
    if info.get("_type") != "playlist":
        return [info]
    entries = info.get("entries")
    if not isinstance(entries, list):
        return []
    return [{str(k): v for k, v in item.items() if v is not None} for item in entries if isinstance(item, dict)]


def video_formats(info: dict[str, object]) -> list[dict[str, object]]:
    formats = info.get("formats")
    if not isinstance(formats, list):
        return []
    return [
        {str(k): v for k, v in item.items() if v is not None}
        for item in formats
        if isinstance(item, dict) and item.get("vcodec") not in (None, "none") and _trusted_media_url(item.get("url"))
    ]


def image_urls(info: dict[str, object]) -> list[str]:
    candidates: list[tuple[int, int, str]] = []
    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list):
        for item in thumbnails:
            if not isinstance(item, dict) or not _trusted_media_url(item.get("url")):
                continue
            width = _dimension(item.get("width"))
            height = _dimension(item.get("height"))
            candidates.append((width * height, len(candidates), str(item["url"])))
    if not candidates:
        return []
    return [max(candidates, key=lambda item: (item[0], item[1]))[2]]


def pick_thumbnail(info: dict[str, object]) -> str | None:
    thumbnails = image_urls(info)
    if thumbnails:
        return thumbnails[0]
    for key in ("thumbnail", "display_url"):
        value = info.get(key)
        if _trusted_media_url(value):
            return str(value)
    return None


def author_avatar(info: dict[str, object]) -> str | None:
    """从 yt-dlp 的频道/作者缩略图中选取可信头像。"""
    authors = info.get("channel_thumbnails") or info.get("uploader_thumbnails")
    if not isinstance(authors, list):
        return None
    for item in reversed(authors):
        if not isinstance(item, dict):
            continue
        value = item.get("url")
        if _trusted_media_url(value):
            return str(value)
    return None


def _dimension(value: object) -> int:
    return int(str(value)) if isinstance(value, int) and not isinstance(value, bool) else 0


def format_selector(quality: str | None) -> str:
    value = (quality or "auto").strip().lower()
    height = value.removesuffix("p")
    if value == "auto" or not height.isdigit():
        return "bv*+ba/b"
    return f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b"


def _http_url(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _trusted_media_url(value: object) -> bool:
    if not _http_url(value):
        return False
    host = (urlparse(str(value)).hostname or "").lower()
    return any(
        host == domain or host.endswith(f".{domain}") for domain in ("instagram.com", "cdninstagram.com", "fbcdn.net")
    )
