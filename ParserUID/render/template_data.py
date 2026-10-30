from __future__ import annotations

from typing import Literal, ClassVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemplateMetric:
    label: str
    value: str
    icon_uri: str


@dataclass(frozen=True, slots=True)
class TemplateReply:
    author_name: str
    text: str
    avatar_uri: str
    parent_author: str | None = None


@dataclass(frozen=True, slots=True)
class TemplateComment:
    author_name: str
    text: str
    avatar_uri: str
    location: str | None = None
    timestamp: str | None = None
    like_icon_uri: str = ""
    like_count: str | None = None
    reply_icon_uri: str = ""
    reply_count: str | None = None
    replies: tuple[TemplateReply, ...] = ()


@dataclass(frozen=True, slots=True)
class TemplateText:
    text: str
    kind: ClassVar[Literal["text"]] = "text"


@dataclass(frozen=True, slots=True)
class TemplateImage:
    uri: str
    caption: str | None = None


@dataclass(frozen=True, slots=True)
class TemplateImageGroup:
    images: tuple[TemplateImage, ...]
    columns: int
    hidden_count: int = 0
    kind: ClassVar[Literal["image_group"]] = "image_group"


TemplateBlock = TemplateText | TemplateImageGroup

CARD_FOOTER = "Create by GsCore & Power by ParserUID"


@dataclass(frozen=True, slots=True)
class TemplateCard:
    """Jinja 模板使用的默认皮肤视图数据。"""

    platform_display_name: str
    platform_logo_uri: str
    title: str
    text: str
    author_name: str
    author_description: str | None
    author_uid: str | None
    author_avatar_uri: str
    author_metrics: tuple[TemplateMetric, ...]
    timestamp: str | None
    source_url: str | None
    hero_uri: str
    ambient_uri: str
    show_hero: bool
    blocks: tuple[TemplateBlock, ...]
    metrics: tuple[TemplateMetric, ...]
    comments: tuple[TemplateComment, ...]
    extra_info: str | None
    repost: TemplateCard | None
    common_meta: tuple[str, ...]
    profile_note: str
    is_video: bool
    footer: str = CARD_FOOTER
    empty_image_placeholder: str = ""
    play_icon_uri: str = ""
    clock_icon_uri: str = ""
    video_duration: str | None = None
    video_quality: str | None = None

    @property
    def images(self) -> tuple[TemplateImage, ...]:
        images: list[TemplateImage] = []
        for block in self.blocks:
            if isinstance(block, TemplateImageGroup):
                images.extend(block.images)
        return tuple(images)
