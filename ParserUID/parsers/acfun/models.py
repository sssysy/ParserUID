"""AcFun 数据模型定义。"""

from __future__ import annotations

import re
import html

import msgspec

from ...utils.utils import format_num
from ...render.models import Metric


class AcfunUser(msgspec.Struct):
    name: str = ""
    head_url: str = msgspec.field(default="", name="headUrl")
    user_id: str = msgspec.field(default="", name="id")
    ip_location: str = msgspec.field(default="", name="ipLocation")


class AcfunPlayInfo(msgspec.Struct):
    quality_label: str = msgspec.field(default="", name="qualityLabel")
    fps: int = 0
    play_urls: list[str] = msgspec.field(default_factory=list, name="playUrls")


class AcfunCurrentVideo(msgspec.Struct):
    play_infos: list[AcfunPlayInfo] = msgspec.field(default_factory=list, name="playInfos")
    duration_millis: int = msgspec.field(default=0, name="durationMillis")
    title: str = ""


class AcfunVideoInfo(msgspec.Struct):
    title: str = ""
    description: str | None = None
    cover_url: str = msgspec.field(default="", name="coverUrl")
    create_time_millis: int = msgspec.field(default=0, name="createTimeMillis")
    duration_millis: int = msgspec.field(default=0, name="durationMillis")
    user: AcfunUser = msgspec.field(default_factory=AcfunUser)
    current_video_info: AcfunCurrentVideo = msgspec.field(default_factory=AcfunCurrentVideo, name="currentVideoInfo")
    view_count: int = msgspec.field(default=0, name="viewCount")
    like_count: int = msgspec.field(default=0, name="likeCount")
    stow_count: int = msgspec.field(default=0, name="stowCount")
    share_count: int = msgspec.field(default=0, name="shareCount")
    comment_count: int = msgspec.field(default=0, name="commentCount")
    banana_count: int = msgspec.field(default=0, name="bananaCount")
    danmaku_count: int = msgspec.field(default=0, name="danmakuCount")

    @property
    def text(self) -> str:
        return clean_description(self.description)

    @property
    def timestamp(self) -> int | None:
        return self.create_time_millis // 1000 if self.create_time_millis else None

    @property
    def duration(self) -> float:
        millis = self.current_video_info.duration_millis or self.duration_millis
        return millis / 1000 if millis else 0.0

    @property
    def metrics(self) -> tuple[Metric, ...]:
        return (
            Metric(label="播放", value=format_num(self.view_count)),
            Metric(label="点赞", value=format_num(self.like_count)),
            Metric(label="评论", value=format_num(self.comment_count)),
            Metric(label="收藏", value=format_num(self.stow_count)),
            Metric(label="分享", value=format_num(self.share_count)),
            Metric(label="香蕉", value=format_num(self.banana_count)),
            Metric(label="弹幕", value=format_num(self.danmaku_count)),
        )


def clean_description(value: str | None) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?p(?:\s[^>]*)?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
