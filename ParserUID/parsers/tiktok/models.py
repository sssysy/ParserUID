"""TikTok yt-dlp 条目、公开页数据与媒体选择模型。"""

from __future__ import annotations

from urllib.parse import urlparse

from msgspec import Struct, field


class TikTokThumbnail(Struct):
    url: str = ""
    width: int | None = None
    height: int | None = None
    preference: int | None = None
    id: str = ""


class TikTokFormat(Struct):
    url: str = ""
    ext: str = ""
    vcodec: str | None = None
    acodec: str | None = None
    width: int | None = None
    height: int | None = None
    tbr: float | None = None
    filesize: int | None = None
    format_id: str = ""
    protocol: str = ""
    preference: int | None = None


class TikTokInfo(Struct):
    id: str = ""
    title: str = ""
    description: str = ""
    uploader: str = ""
    uploader_id: str = ""
    channel: str = ""
    channel_id: str = ""
    author_avatar: str = ""
    duration: float = 0.0
    timestamp: int | None = None
    webpage_url: str = ""
    thumbnail: str = ""
    thumbnails: list[TikTokThumbnail] = []
    images: list[TikTokThumbnail] = []
    formats: list[TikTokFormat] = []
    entries: list[TikTokInfo] = []
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    repost_count: int | None = None
    save_count: int | None = None

    @property
    def is_photo_post(self) -> bool:
        return bool(self.images)


class TikTokPageUrlList(Struct):
    url_list: list[str] = field(name="urlList", default_factory=list)


class TikTokPageAuthor(Struct):
    id: str = ""
    unique_id: str = field(name="uniqueId", default="")
    nickname: str = ""
    avatar_larger: TikTokPageUrlList = field(name="avatarLarger", default_factory=TikTokPageUrlList)
    avatar_medium: TikTokPageUrlList = field(name="avatarMedium", default_factory=TikTokPageUrlList)
    avatar_thumb: TikTokPageUrlList = field(name="avatarThumb", default_factory=TikTokPageUrlList)


class TikTokPageStatistics(Struct):
    digg_count: int | str = field(name="diggCount", default=0)
    comment_count: int | str = field(name="commentCount", default=0)
    share_count: int | str = field(name="shareCount", default=0)
    play_count: int | str = field(name="playCount", default=0)
    collect_count: int | str = field(name="collectCount", default=0)


class TikTokPageImage(Struct):
    image_url: TikTokPageUrlList = field(name="imageURL", default_factory=TikTokPageUrlList)
    image_width: int | str = field(name="imageWidth", default=0)
    image_height: int | str = field(name="imageHeight", default=0)


class TikTokImagePost(Struct):
    title: str = ""
    images: list[TikTokPageImage] = []


class TikTokPagePlayAddress(Struct):
    url_list: list[str] = field(name="UrlList", default_factory=list)
    data_size: int | str = field(name="DataSize", default=0)
    width: int | str = field(name="Width", default=0)
    height: int | str = field(name="Height", default=0)


class TikTokPageBitrate(Struct):
    play_addr: TikTokPagePlayAddress = field(name="PlayAddr", default_factory=TikTokPagePlayAddress)
    bit_rate: int | str = field(name="Bitrate", default=0)
    gear_name: str = field(name="GearName", default="")


class TikTokPageVideo(Struct):
    duration: int | str = 0
    play_addr: TikTokPageUrlList = field(name="playAddr", default_factory=TikTokPageUrlList)
    download_addr: TikTokPageUrlList = field(name="downloadAddr", default_factory=TikTokPageUrlList)
    cover: TikTokPageUrlList = field(default_factory=TikTokPageUrlList)
    origin_cover: TikTokPageUrlList = field(name="originCover", default_factory=TikTokPageUrlList)
    dynamic_cover: TikTokPageUrlList = field(name="dynamicCover", default_factory=TikTokPageUrlList)
    bitrate_info: list[TikTokPageBitrate] = field(name="bitrateInfo", default_factory=list)


class TikTokPageItem(Struct):
    id: str = ""
    desc: str = ""
    create_time: int | str = field(name="createTime", default=0)
    author: TikTokPageAuthor = field(default_factory=TikTokPageAuthor)
    statistics: TikTokPageStatistics = field(default_factory=TikTokPageStatistics)
    video: TikTokPageVideo | None = None
    image_post: TikTokImagePost | None = field(name="imagePost", default=None)


class TikTokPageItemInfo(Struct):
    item_struct: TikTokPageItem | None = field(name="itemStruct", default=None)


class TikTokPageVideoDetail(Struct):
    item_info: TikTokPageItemInfo | None = field(name="itemInfo", default=None)


class TikTokPageWebapp(Struct):
    video_detail: TikTokPageVideoDetail | None = field(name="video-detail", default=None)


class TikTokDefaultScope(Struct):
    webapp: TikTokPageWebapp = field(default_factory=TikTokPageWebapp)


class TikTokUniversalData(Struct):
    default_scope: TikTokDefaultScope = field(name="__DEFAULT_SCOPE__", default_factory=TikTokDefaultScope)


def trusted_media_url(value: str) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower()
    return any(
        host == domain or host.endswith(f".{domain}")
        for domain in (
            "tiktok.com",
            "tiktokcdn.com",
            "tiktokcdn-us.com",
            "tiktokv.com",
            "ibytedtos.com",
            "byteoversea.com",
            "muscdn.com",
        )
    )


def best_thumbnail(images: list[TikTokThumbnail]) -> str | None:
    candidates = [item for item in images if trusted_media_url(item.url)]
    if not candidates:
        return None
    return max(candidates, key=lambda item: ((item.width or 0) * (item.height or 0), item.preference or 0)).url


def image_urls(images: list[TikTokThumbnail]) -> list[str]:
    return [item.url for item in images if trusted_media_url(item.url)]


def video_formats(formats: list[TikTokFormat]) -> list[TikTokFormat]:
    return [item for item in formats if item.vcodec not in (None, "none") and trusted_media_url(item.url)]


def audio_formats(formats: list[TikTokFormat]) -> list[TikTokFormat]:
    return [
        item
        for item in formats
        if item.acodec not in (None, "none") and item.vcodec in (None, "none") and trusted_media_url(item.url)
    ]


def _quality_height(item: TikTokFormat) -> int:
    return max(item.height or 0, item.width or 0)


def select_video(formats: list[TikTokFormat], quality: str) -> TikTokFormat | None:
    candidates = video_formats(formats)
    if not candidates:
        return None
    value = quality.lower().removesuffix("p")
    height = int(value) if value.isdigit() else 0
    eligible = [item for item in candidates if not height or _quality_height(item) <= height]
    pool = eligible or candidates
    return max(pool, key=lambda item: (_quality_height(item), item.tbr or 0, item.filesize or 0))


def video_candidates(formats: list[TikTokFormat], quality: str) -> list[TikTokFormat]:
    """按画质优先返回去重后的有限视频候选。"""
    selected = select_video(formats, quality)
    candidates = video_formats(formats)
    if selected is None:
        return []
    selected_height = _quality_height(selected)
    ordered = sorted(
        candidates,
        key=lambda item: (
            abs(_quality_height(item) - selected_height),
            -_quality_height(item),
            -(item.tbr or 0),
            -(item.filesize or 0),
        ),
    )
    result: list[TikTokFormat] = []
    seen: set[str] = set()
    for item in ordered:
        if item.url in seen:
            continue
        seen.add(item.url)
        result.append(item)
        if len(result) >= 3:
            break
    return result


def select_audio(formats: list[TikTokFormat]) -> TikTokFormat | None:
    candidates = audio_formats(formats)
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.tbr or 0, item.filesize or 0))


def parse_int(value: int | str | None) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
