from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class Metric:
    """卡片上展示的一组标签与数值。"""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class Author:
    """卡片作者信息，可附带本地头像。"""

    name: str
    description: str | None = None
    uid: str | None = None
    avatar_path: Path | None = None
    avatar_url: str | None = None
    metrics: tuple[Metric, ...] = ()


@dataclass(frozen=True, slots=True)
class ImageBlock:
    """放在正文原始位置的图片。"""

    path: Path | None = None
    url: str | None = None
    caption: str | None = None

    def __post_init__(self) -> None:
        if (self.path is None) == (self.url is None):
            raise ValueError("图片块必须且只能提供本地路径或远程地址")


@dataclass(frozen=True, slots=True)
class TextBlock:
    """放在正文原始位置的文本。"""

    text: str


CardBlock = TextBlock | ImageBlock


@dataclass(frozen=True, slots=True)
class CommentReply:
    """根评论下方展示的一条一级回复。"""

    author_name: str
    text: str
    avatar_path: Path | None = None
    avatar_url: str | None = None
    parent_author: str | None = None


@dataclass(frozen=True, slots=True)
class Comment:
    """一条根评论及其可选的一级回复。"""

    author_name: str
    text: str
    avatar_path: Path | None = None
    avatar_url: str | None = None
    location: str | None = None
    timestamp: str | None = None
    like_count: str | None = None
    reply_count: str | None = None
    replies: tuple[CommentReply, ...] = ()


@dataclass(frozen=True, slots=True)
class PreviewCard:
    """渲染器接受的平台无关卡片数据。"""

    platform_name: str
    platform_display_name: str
    title: str
    text: str = ""
    author: Author | None = None
    timestamp: str | None = None
    source_url: str | None = None
    cover_path: Path | None = None
    cover_url: str | None = None
    blocks: tuple[CardBlock, ...] = ()
    metrics: tuple[Metric, ...] = ()
    comments: tuple[Comment, ...] = ()
    extra_info: str | None = None
    repost: PreviewCard | None = None
    video_duration: str | None = None
    video_quality: str | None = None
    card_type: str = "common"

    @property
    def is_video(self) -> bool:
        """返回是否应使用视频版式。"""
        return self.card_type == "video" or self.video_duration is not None or self.video_quality is not None

    @property
    def image_paths(self) -> Sequence[Path]:
        """按展示顺序返回已下载到本地的正文图片。"""
        return tuple(block.path for block in self.blocks if isinstance(block, ImageBlock) and block.path is not None)
