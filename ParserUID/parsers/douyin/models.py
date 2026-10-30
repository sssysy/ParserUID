"""抖音数据结构与序列化模型。"""

from __future__ import annotations

import json
from dataclasses import dataclass

import msgspec
from msgspec import Struct, field

from ...utils.utils import format_num
from ...render.models import Metric


class UrlList(Struct):
    url_list: list[str] = field(default_factory=list)


class Author(Struct):
    uid: str = ""
    sec_uid: str = ""
    nickname: str = ""
    signature: str | None = None
    unique_id: str = ""
    short_id: str = ""
    total_favorited: int | str = 0
    following_count: int | str = 0
    follower_count: int | str = 0
    avatar_thumb: UrlList | None = None
    avatar_medium: UrlList | None = None
    avatar_larger: UrlList | None = None


class Statistics(Struct):
    digg_count: int | str = 0
    comment_count: int | str = 0
    collect_count: int | str = 0
    share_count: int | str = 0
    recommend_count: int | str = 0
    play_count: int | str = 0


class PlayAddr(Struct):
    uri: str = ""
    url_list: list[str] = field(default_factory=list)
    data_size: int = 0
    width: int = 0
    height: int = 0

    @property
    def urls(self) -> list[str]:
        return [url for url in self.url_list if url]


class Cover(Struct):
    url_list: list[str] = field(default_factory=list)


class BitRate(Struct):
    play_addr: PlayAddr = field(default_factory=PlayAddr)
    format: str = ""
    gear_name: str = ""
    is_h265: int = 0
    is_bytevc1: int = 0
    bit_rate: int = 0
    FPS: int | str = 0
    HDR_type: int | str = ""
    video_extra: str = ""

    @property
    def definition(self) -> str:
        try:
            extra = json.loads(self.video_extra)
        except (TypeError, ValueError):
            return ""
        definition = extra.get("definition") if isinstance(extra, dict) else None
        return definition if isinstance(definition, str) else ""


class Video(Struct):
    play_addr: PlayAddr = field(default_factory=PlayAddr)
    cover: Cover = field(default_factory=Cover)
    cover_original_scale: Cover | None = None
    origin_cover: Cover | None = None
    animated_cover: Cover | None = None
    duration: int = 0
    bit_rate: list[BitRate] = field(default_factory=list)


class Image(Struct):
    uri: str = ""
    url_list: list[str] = field(default_factory=list)
    clip_type: int | None = None
    video: Video | None = None


class MusicPlayUrl(Struct):
    uri: str = ""
    url_list: list[str] = field(default_factory=list)


class Music(Struct):
    duration: int = 0
    mid: str = ""
    title: str = ""
    author: str = ""
    play_url: MusicPlayUrl = field(default_factory=MusicPlayUrl)
    extra: str | None = None


class ArticleInfo(Struct):
    article_title: str | None = None
    article_content: str | None = None


class ShareInfo(Struct):
    share_url: str = ""
    share_desc: str = ""
    share_title: str = ""


class Aweme(Struct):
    aweme_id: str = ""
    aweme_type: int = 0
    author: Author = field(default_factory=Author)
    share_info: ShareInfo = field(default_factory=ShareInfo)
    statistics: Statistics = field(default_factory=Statistics)
    create_time: int = 0
    share_url: str = ""
    desc: str = ""
    preview_title: str = ""
    region: str = ""
    is_slides: bool = False
    images: list[Image] | None = None
    music: Music | None = None
    video: Video | None = None
    article_info: ArticleInfo | None = None

    @property
    def is_video(self) -> bool:
        return self.aweme_type in {0, 4, 55} and not self.images and self.video is not None

    @property
    def is_article(self) -> bool:
        return self.aweme_type == 163 or self.article_info is not None

    @property
    def short_url(self) -> str:
        if self.aweme_id:
            kind = "article" if self.is_article else ("video" if self.is_video else "note")
            return f"https://www.douyin.com/{kind}/{self.aweme_id}"
        return self.share_url.split("?", 1)[0]

    @property
    def avatar_url(self) -> str | None:
        for avatar in (self.author.avatar_larger, self.author.avatar_medium, self.author.avatar_thumb):
            if avatar and avatar.url_list:
                return avatar.url_list[0]
        return None

    @property
    def author_id(self) -> str | None:
        return self.author.unique_id or self.author.short_id or self.author.uid or None

    @property
    def metrics(self) -> tuple[Metric, ...]:
        def _parse_num(val: int | str | None) -> int:
            try:
                return int(val or 0)
            except (ValueError, TypeError):
                return 0

        stats = self.statistics
        return (
            Metric(label="点赞", value=format_num(_parse_num(stats.digg_count))),
            Metric(label="评论", value=format_num(_parse_num(stats.comment_count))),
            Metric(label="收藏", value=format_num(_parse_num(stats.collect_count))),
            Metric(label="分享", value=format_num(_parse_num(stats.share_count))),
        )

    @property
    def cover_url(self) -> str | None:
        if self.images:
            for image in self.images:
                if image.url_list:
                    return image.url_list[0]
        video = self.video
        if video is None:
            return None
        for cover in (video.animated_cover, video.cover_original_scale, video.origin_cover, video.cover):
            if cover and cover.url_list:
                return cover.url_list[0]
        return None


class VideoInfoRes(Struct):
    item_list: list[Aweme] = field(default_factory=list)

    @property
    def aweme(self) -> Aweme:
        if not self.item_list:
            raise ValueError("videoInfoRes 中未找到作品")
        return self.item_list[0]


class VideoOrNotePage(Struct):
    video_info_res: VideoInfoRes = field(name="videoInfoRes", default_factory=VideoInfoRes)


class LoaderData(Struct):
    video_page: VideoOrNotePage | None = field(name="video_(id)/page", default=None)
    note_page: VideoOrNotePage | None = field(name="note_(id)/page", default=None)


class RouterData(Struct):
    loader_data: LoaderData = field(name="loaderData", default_factory=LoaderData)

    @property
    def aweme(self) -> Aweme:
        if self.loader_data.video_page is not None:
            return self.loader_data.video_page.video_info_res.aweme
        if self.loader_data.note_page is not None:
            return self.loader_data.note_page.video_info_res.aweme
        raise ValueError("路由数据中未找到视频或图文页面")


class Owner(Struct):
    id_str: str = ""
    nickname: str = ""
    avatar_thumb: UrlList = field(default_factory=UrlList)


class RoomViewStats(Struct):
    display_value: int | str = 0


class Room(Struct):
    id_str: str = ""
    status: int = 0
    title: str = ""
    cover: UrlList = field(default_factory=UrlList)
    owner: Owner = field(default_factory=Owner)
    room_view_stats: RoomViewStats = field(default_factory=RoomViewStats)
    like_count: int | str = 0


class CommentUser(Struct):
    uid: str = ""
    nickname: str = ""
    avatar_thumb: UrlList = field(default_factory=UrlList)


class CommentItem(Struct):
    cid: str = ""
    aweme_id: str = ""
    digg_count: int | str = 0
    text: str = ""
    user: CommentUser = field(default_factory=CommentUser)
    create_time: int = 0
    ip_label: str | None = None
    reply_comment_total: int | str = 0


class CommentResponse(Struct):
    comments: list[CommentItem] | None = None


@dataclass(frozen=True, slots=True)
class SelectedVideo:
    bit_rate: BitRate
    url: str
    fallback_urls: list[str]
    quality: str
    resolution: str


QUALITY_LABELS = {
    "4k": "超清4K",
    "2k": "超清2K",
    "1440p": "超清2K",
    "1080p": "高清1080P",
    "720p": "高清720P",
    "540p": "标清540P",
}


def decode_aweme(payload: bytes | str | dict[str, object], *, detail: bool) -> Aweme:
    raw = msgspec.json.decode(payload) if isinstance(payload, (bytes, str)) else payload
    if not isinstance(raw, dict):
        raise msgspec.ValidationError("响应数据不是有效对象")
    if detail:
        detail_obj = raw.get("aweme_detail")
        aweme = msgspec.convert(detail_obj, type=Aweme)
    else:
        aweme = msgspec.convert(raw, type=RouterData).aweme
    if not aweme.aweme_id:
        raise msgspec.ValidationError("响应中缺少 aweme_id")
    return aweme


def select_video(aweme: Aweme, quality: str = "auto") -> SelectedVideo | None:
    video = aweme.video
    if video is None:
        return None
    candidates = [item for item in video.bit_rate if item.format == "mp4" and item.play_addr.url_list]
    if not candidates and video.play_addr.url_list:
        candidates = [BitRate(play_addr=video.play_addr, format="mp4")]
    if not candidates:
        return None

    def level(item: BitRate) -> int:
        definition = (
            item.definition or item.play_addr.uri or item.gear_name or " ".join(item.play_addr.urls[:1])
        ).lower()
        for index, key in enumerate(("lowest_4", "2160", "4k", "1440", "2k", "1080", "720", "540")):
            if key in definition:
                return index
        return 99

    if quality in {"auto", "adapt"}:
        selected = max(candidates, key=lambda item: (level(item), item.play_addr.data_size, not bool(item.is_bytevc1)))
    else:
        selected = next((item for item in candidates if quality in (item.definition, item.gear_name)), None)
        if selected is None:
            selected = max(candidates, key=lambda item: item.play_addr.data_size)

    play_addr = selected.play_addr
    urls = play_addr.urls
    definition = (
        selected.definition or selected.play_addr.uri or selected.gear_name or " ".join(selected.play_addr.urls[:1])
    )
    label = next((QUALITY_LABELS[key] for key in QUALITY_LABELS if key in definition.lower()), definition or "视频")
    resolution = f"{play_addr.width} x {play_addr.height}" if play_addr.width and play_addr.height else ""
    return SelectedVideo(
        bit_rate=selected,
        url=urls[0],
        fallback_urls=urls[1:],
        quality=label,
        resolution=resolution,
    )
