"""ParserUID 跨平台数据模型与内容项定义。"""

from __future__ import annotations

import asyncio
from typing import Literal
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable, Awaitable

from ..sender import MediaKind, ContentItem, ResultSummary
from .music_card import MusicCardInfo
from ..render.models import (
    PreviewCard,
)

PathLoader = Callable[[], Awaitable[Path]]
Description = PreviewCard


async def _call_loader(loader: PathLoader) -> Path:
    return await loader()


@dataclass(frozen=True, slots=True)
class Platform:
    """平台身份标识，目录名、配置名与此名称必须保持一致。"""

    name: str
    display_name: str


@dataclass(frozen=True, slots=True)
class TextContent:
    """纯文本内容项，参与有序结果队列与发送。"""

    _text: str

    def kind(self) -> Literal["text"]:
        return "text"

    def text(self) -> str:
        return self._text

    @property
    def content(self) -> str:
        return self._text


class LyricContent(TextContent):
    """歌词文本项；音乐卡片已发送时由发送层跳过。"""

    @property
    def is_lyric(self) -> Literal[True]:
        return True


class MediaContent:
    """可惰性加载本地路径的媒体内容基类。"""

    def __init__(
        self,
        path: Path | None = None,
        loader: PathLoader | None = None,
    ) -> None:
        self._path = path
        self._loader = loader
        self._task: asyncio.Task[Path] | None = None

    def kind(self) -> MediaKind:
        raise NotImplementedError

    def set_loader(self, loader: PathLoader) -> None:
        """设置异步加载函数。"""
        self._loader = loader
        self._task = None

    async def path(self) -> Path:
        """获取或解析媒体的本地物理路径。"""
        if self._path is not None:
            return self._path
        if self._loader is None and self._task is None:
            raise RuntimeError("媒体内容未提供文件路径或异步加载器")
        if self._task is None and self._loader is not None:
            self._task = asyncio.create_task(_call_loader(self._loader))
            self._loader = None
        if self._task is not None:
            self._path = await self._task
        if self._path is None:
            raise RuntimeError("异步加载器未能返回有效的本地文件路径")
        return self._path

    async def get_path(self) -> Path:
        """兼容原解析器代码习惯的方法别名。"""
        return await self.path()

    @property
    def duration(self) -> float:
        """返回媒体时长；未知时为 0。"""
        return 0.0


class ImageContent(MediaContent):
    """图片媒体内容项。"""

    def kind(self) -> Literal["image"]:
        return "image"


class VideoContent(MediaContent):
    """视频媒体内容项。"""

    def __init__(
        self,
        path: Path | None = None,
        loader: PathLoader | None = None,
        *,
        cover: Path | None = None,
        duration: float = 0.0,
    ) -> None:
        super().__init__(path, loader)
        self.cover = cover
        self.fallback_urls: list[str] = []
        self._duration = duration

    def kind(self) -> Literal["video"]:
        return "video"

    @property
    def duration(self) -> float:
        return self._duration

    async def get_cover_path(self) -> Path | None:
        return self.cover


class AudioContent(MediaContent):
    """音频媒体内容项。"""

    def __init__(
        self,
        path: Path | None = None,
        loader: PathLoader | None = None,
        *,
        duration: float = 0.0,
        music_card: MusicCardInfo | None = None,
    ) -> None:
        super().__init__(path, loader)
        self._duration = duration
        self.music_card = music_card

    def kind(self) -> Literal["audio"]:
        return "audio"

    @property
    def duration(self) -> float:
        return self._duration


class FileContent(MediaContent):
    """文件媒体内容项。"""

    def __init__(
        self,
        path: Path | None = None,
        loader: PathLoader | None = None,
        *,
        name: str | None = None,
    ) -> None:
        super().__init__(path, loader)
        self.name = name

    def kind(self) -> Literal["file"]:
        return "file"


class LivePhotoContent(MediaContent):
    """Live Photo（静态首图与实况短视频合成）媒体。"""

    def __init__(
        self,
        path: Path | None = None,
        loader: PathLoader | None = None,
        *,
        image_path: Path | None = None,
        cover: Path | None = None,
        duration: float = 0.0,
        loop: int = 3,
    ) -> None:
        super().__init__(path, loader)
        self.image_path = image_path
        self.cover = cover
        self._duration = duration
        self.loop = loop

    def kind(self) -> Literal["video"]:
        return "video"

    @property
    def duration(self) -> float:
        return self._duration

    async def get_image_path(self) -> Path | None:
        return self.image_path


class DynamicContent(MediaContent):
    """动态图文/动图媒体内容项。"""

    def __init__(
        self,
        path: Path | None = None,
        loader: PathLoader | None = None,
        *,
        gif_path: Path | None = None,
    ) -> None:
        super().__init__(path, loader)
        self.gif_path = gif_path

    def kind(self) -> Literal["video"]:
        return "video"


@dataclass(slots=True)
class VideoMeta:
    """视频卡片展示元数据。"""

    cover: Path | None = None
    duration: float = 0.0
    quality: str | None = None
    resolution: str | None = None

    @property
    def display_duration(self) -> str | None:
        if self.duration <= 0:
            return None
        minutes = int(self.duration) // 60
        seconds = int(self.duration) % 60
        return f"{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True, slots=True)
class ParseResult:
    """有序内容列表和可选的发送摘要。"""

    contents: tuple[ContentItem, ...]
    summary: ResultSummary | None = None
