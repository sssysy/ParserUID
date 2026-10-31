from __future__ import annotations

import asyncio
from typing import Literal, Protocol, TypeAlias, runtime_checkable
from pathlib import Path
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from collections.abc import Sequence

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event, Message
from gsuid_core.segment import MessageSegment
from gsuid_core.utils.plugins_config.gs_config import sp_config

from .config import (
    parser_config,
    default_skin_pack,
    max_file_size_bytes,
    music_as_voice_enabled,
    max_audio_duration_seconds,
    max_video_duration_seconds,
)
from .utils.ffmpeg import probe_duration
from .render.models import PreviewCard
from .utils.exceptions import ResourceLimitException, sanitize_error_text

_CURRENT_SEND_PROXY: ContextVar[str | None] = ContextVar("parser_uid_send_proxy", default=None)


def send_proxy_context() -> ContextVar[str | None]:
    """返回当前发送链路的代理上下文，供解析器调度临时设置。"""
    return _CURRENT_SEND_PROXY


MediaKind: TypeAlias = Literal["image", "video", "audio", "file"]
ContentKind: TypeAlias = Literal["text", "image", "video", "audio", "file"]

DEFAULT_FORWARD_THRESHOLD = 3
MEDIA_KINDS: tuple[MediaKind, ...] = ("image", "video", "audio", "file")
CONTENT_KINDS: tuple[ContentKind, ...] = ("text", *MEDIA_KINDS)
MEDIA_LABELS: dict[MediaKind, str] = {
    "image": "图片",
    "video": "视频",
    "audio": "音频",
    "file": "文件",
}


@runtime_checkable
class ContentProtocol(Protocol):
    def kind(self) -> ContentKind: ...


@runtime_checkable
class TextContentProtocol(ContentProtocol, Protocol):
    def kind(self) -> Literal["text"]: ...

    def text(self) -> str: ...


@runtime_checkable
class MediaContentProtocol(ContentProtocol, Protocol):
    def kind(self) -> MediaKind: ...

    async def path(self) -> Path: ...

    @property
    def duration(self) -> float: ...


@runtime_checkable
class NamedMediaProtocol(Protocol):
    name: str | None


ContentItem: TypeAlias = TextContentProtocol | MediaContentProtocol


@dataclass(frozen=True, slots=True)
class ResultSummary:
    """纯文字回退所需的公共描述字段。"""

    title: str = ""
    author: str = ""
    text: str = ""
    source_url: str = ""


async def send_description(
    bot: Bot,
    ev: Event,
    description: PreviewCard,
) -> None:
    """通过插件渲染器生成并发送描述卡片。"""
    del ev
    from .render.runtime import render_preview

    image_path = await render_preview(
        description,
        skin=default_skin_pack(),
        proxy=_CURRENT_SEND_PROXY.get(),
    )
    if image_path is None:
        logger.warning("[Parser·发送] 描述卡片渲染失败")
        return
    await send_preview_card(bot, image_path)


def build_text_fallback(
    summary: ResultSummary | None,
    content_count: int,
) -> str:
    """构建不含媒体内容的简短文字摘要。"""
    lines: list[str] = []
    if summary is not None:
        title = _single_line(summary.title)
        author = _single_line(summary.author)
        body = summary.text.strip()
        source_url = _safe_source_url(summary.source_url)
        if title:
            lines.append(f"标题：{title}")
        if author:
            lines.append(f"作者：{author}")
        if body:
            lines.append(f"正文：{body}")
        if source_url:
            lines.append(f"来源：{source_url}")
    lines.append(f"内容数量：{content_count}")
    return "\n".join(lines)


async def send_preview_card(bot: Bot, image_path: Path) -> bool:
    """发送已由渲染层生成的本地预览卡片。"""
    try:
        segment = await asyncio.to_thread(MessageSegment.image, image_path)
    except OSError as exc:
        logger.error(f"[Parser·发送] 预览卡片读取失败 | error_type={type(exc).__name__}")
        return False

    return await _send(bot, segment)


async def send_parse_result(
    bot: Bot,
    contents: Sequence[ContentItem],
    *,
    summary: ResultSummary | None = None,
    forward_threshold: int = DEFAULT_FORWARD_THRESHOLD,
    supports_node: bool = True,
) -> bool:
    """按输入顺序发送解析结果；返回是否已交给 Core 发送队列。"""
    _validate_contents(contents)
    if not contents:
        logger.info("[Parser·发送] 解析结果为空，不发送")
        return False
    if forward_threshold < 0:
        raise ValueError("合并转发阈值不能为负数")

    should_merge = forward_threshold > 0 and len(contents) >= forward_threshold
    if should_merge and not supports_node:
        logger.warning(f"[Parser·发送] 合并转发不可用，发送文字摘要 | count={len(contents)}")
        fallback = build_text_fallback(summary, len(contents))
        return await _send(bot, MessageSegment.text(fallback))

    segments, failures = await _build_segments(contents)
    if not segments and not failures:
        logger.warning("[Parser·发送] 没有可发送的内容")
        return False

    sent = False
    if should_merge and segments:
        logger.info(f"[Parser·发送] 使用合并转发 | count={len(contents)}")
        sent = await _send(bot, [MessageSegment.node(segments)])
    elif segments:
        sent = await _send(bot, segments)

    if failures:
        # 失败提示独立发送，避免被合并转发适配器吞掉或延迟展示。
        failure_sent = await _send(bot, MessageSegment.text("\n".join(failures)))
        sent = failure_sent or sent
    return sent


def _validate_contents(contents: Sequence[ContentItem]) -> None:
    for item in contents:
        kind = item.kind()
        if kind not in CONTENT_KINDS:
            raise TypeError(f"不支持的内容类型：{kind}")
        if kind == "text":
            if not isinstance(item, TextContentProtocol):
                raise TypeError("文本内容必须提供 text()")
            continue
        if not isinstance(item, MediaContentProtocol):
            raise TypeError("媒体内容必须提供 path()")


async def _build_segments(
    contents: Sequence[ContentItem],
) -> tuple[list[Message], list[str]]:
    segments: list[Message] = []
    failures: list[str] = []
    for item in contents:
        kind = item.kind()
        if kind == "text":
            if not isinstance(item, TextContentProtocol):
                raise TypeError("文本内容必须提供 text()")
            text = item.text()
            if text:
                segments.append(MessageSegment.text(text))
            continue

        if not isinstance(item, MediaContentProtocol):
            raise TypeError("媒体内容必须提供 path()")
        built = await _build_media_segment(item, kind)
        if built.segment is not None:
            segments.append(built.segment)
        if built.failure is not None:
            failures.append(built.failure)
    return segments, failures


@dataclass(frozen=True, slots=True)
class _BuiltMediaSegment:
    segment: Message | None = None
    failure: str | None = None


async def _build_media_segment(
    item: MediaContentProtocol,
    kind: MediaKind,
) -> _BuiltMediaSegment:
    # 单项下载失败不能阻断后续内容，边界内只记录异常类型。
    try:
        path = await item.path()
        await _validate_media_limits(path, item.duration, kind)
        if kind == "image":
            return _BuiltMediaSegment(segment=await asyncio.to_thread(MessageSegment.image, path))
        file_name = item.name if isinstance(item, NamedMediaProtocol) and item.name else None
        return _BuiltMediaSegment(segment=await asyncio.to_thread(_media_segment, path, kind, file_name))
    except Exception as exc:
        error_text = sanitize_error_text(str(exc))
        logger.warning(f"[Parser·发送] {MEDIA_LABELS[kind]}内容不可用 | error_type={type(exc).__name__}")
        return _BuiltMediaSegment(failure=f"{MEDIA_LABELS[kind]}获取失败，已跳过：{error_text}")


async def _validate_media_limits(path: Path, duration: float, kind: MediaKind) -> None:
    size = path.stat().st_size if path.is_file() else 0
    max_bytes = max_file_size_bytes()
    if size > max_bytes:
        raise ResourceLimitException(f"文件大小超出限制（上限 {max_bytes // 1024 // 1024} MB）")
    if kind not in {"video", "audio"}:
        return
    limit = max_video_duration_seconds() if kind == "video" else max_audio_duration_seconds()
    actual = duration if duration > 0 else 0.0
    if actual <= 0:
        probed = await probe_duration(path)
        actual = probed if probed is not None and probed > 0 else 0.0
    if actual <= 0:
        return
    if actual > limit:
        raise ResourceLimitException(f"{MEDIA_LABELS[kind]}时长超出限制（上限 {int(limit // 60)} 分钟）")


def _media_segment(path: Path, kind: MediaKind, file_name: str | None = None) -> Message:
    if not path.is_file():
        raise FileNotFoundError("媒体文件不可用")
    file_uri = path.resolve().as_uri()
    match kind:
        case "image":
            raise ValueError("图片消息段不在此函数中构建")
        case "video":
            return Message(type="video", data=file_uri)
        case "audio":
            if music_as_voice_enabled():
                return Message(type="record", data=file_uri)
            name = file_name or path.name
            return Message(type="file", data=f"{name}|{file_uri}")
        case "file":
            name = file_name or path.name
            return Message(type="file", data=f"{name}|{file_uri}")
    raise ValueError("不支持的媒体类型")


async def _send(bot: Bot, payload: Message | list[Message]) -> bool:
    try:
        _ = await bot.send(payload)
    except Exception as exc:
        logger.error(f"[Parser·发送] 消息发送失败 | error_type={type(exc).__name__}")
        return False
    return True


async def send_result_for_event(
    bot: Bot,
    ev: Event,
    contents: Sequence[ContentItem],
    *,
    summary: ResultSummary | None = None,
) -> bool:
    """按单个事件使用 ParserUID 当前生效的配置发送。"""
    return await send_parse_result(
        bot,
        contents,
        summary=summary,
        forward_threshold=max(0, int(parser_config.get_config("node_threshold").data)),
        supports_node=str(sp_config.get_config("EnableForwardMessage").data) in {"允许", "合并为一条消息"},
    )


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _safe_source_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return ""

    host = parsed.hostname
    if parsed.scheme not in ("http", "https") or not host:
        return ""
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
