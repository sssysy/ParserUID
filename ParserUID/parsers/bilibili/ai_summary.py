"""哔哩哔哩视频 AI 总结的 AI 工具声明与实现。"""

from __future__ import annotations

import re
from typing import Iterator, Protocol
from collections.abc import Sequence

from pydantic_ai import RunContext

from gsuid_core.ai_core.models import ToolContext

from .summary import extract_first_bilibili_url
from ...utils.ai_models import AiToolSpec
from ...utils.exceptions import ParseException, sanitize_error_text

_BILIBILI_HINT_RE = re.compile(
    r"(?:bilibili\.com|b23\.tv|bili2233\.cn|(?<![0-9A-Za-z])BV[0-9A-Za-z]{8,15}|(?<![0-9A-Za-z])[aA][vV]\d+)",
    re.IGNORECASE,
)
_SHARE_SEGMENT_TYPES = frozenset({"text", "json", "xml", "share", "card", "ark", "light_app"})


class _VisibilityScanCtx(Protocol):
    @property
    def deps(self) -> ToolContext | None: ...

    @property
    def messages(self) -> Sequence[object]: ...


def _iter_context_texts(ctx: _VisibilityScanCtx) -> Iterator[str]:
    ev = ctx.deps.ev if ctx.deps is not None else None
    if ev is not None:
        yield ev.text
        yield ev.raw_text
    for message in ctx.messages:
        yield str(message)


def _context_mentions_bilibili(ctx: _VisibilityScanCtx) -> bool:
    """只在上下文出现 B 站链接、BV 号或 AV 号时暴露该工具。"""
    if ctx.deps is None:
        return True
    for text in _iter_context_texts(ctx):
        if _BILIBILI_HINT_RE.search(text):
            return True
    return False


def _event_text_candidates(ev: object) -> list[str]:
    """收集当前消息正文与分享卡片文本，供工具定位视频链接。"""
    texts: list[str] = []
    raw_text = getattr(ev, "raw_text", "")
    if isinstance(raw_text, str) and raw_text.strip():
        texts.append(raw_text)
    content = getattr(ev, "content", ())
    if isinstance(content, Sequence):
        for segment in content:
            if getattr(segment, "type", "") not in _SHARE_SEGMENT_TYPES:
                continue
            data = getattr(segment, "data", None)
            if data is not None:
                texts.append(str(data))
    return texts


def _target_from_context(ctx: RunContext[ToolContext], url: str) -> str | None:
    target = url.strip()
    if target:
        return target
    ev = ctx.deps.ev
    if ev is None:
        return None
    for text in _event_text_candidates(ev):
        found = extract_first_bilibili_url(text)
        if found is not None:
            return found
    return extract_first_bilibili_url(ev.raw_text)


async def get_bilibili_ai_summary(
    ctx: RunContext[ToolContext],
    url: str = "",
    page: int = 0,
) -> str:
    """获取哔哩哔哩视频的官方 AI 总结。

    用户要求“总结这个 B 站视频”“这个视频讲了什么”且上下文带有哔哩哔哩
    链接、BV 号或 AV 号时调用。返回格式会明确标注“官方 AI 总结可用性”：
    可用时返回官方总结与章节大纲；不可用时说明原因，并注明后续内容只是
    视频基础信息和简介，不得对外称为官方 AI 总结，也不得据此推断视频正文。

    Args:
        url: 哔哩哔哩视频链接、b23.tv 短链、BV 号或 AV 号；留空时从当前消息提取。
        page: 分 P 序号，从 1 开始；0 表示使用链接中的 p 参数或第 1 集。
    """
    target = _target_from_context(ctx, url)
    if target is None:
        return (
            "❌ 当前上下文没有哔哩哔哩视频链接、BV 号或 AV 号。"
            "只能看到视频简介时无法获取官方 AI 总结，不能把简介当作总结"
        )

    from .parser import BilibiliParser

    parser = await BilibiliParser.active_instance()
    if parser is None:
        return "❌ 哔哩哔哩解析器未启用，请在解析器设置中启用后再使用 AI 总结"

    try:
        return await parser.fetch_video_summary_text(target, page=page)
    except ParseException as exc:
        detail = sanitize_error_text(str(exc), hide_local_paths=True)
        return f"❌ 哔哩哔哩 AI 总结获取失败：{detail or '请稍后再试'}"


AI_TOOLS: tuple[AiToolSpec, ...] = (
    AiToolSpec(
        func=get_bilibili_ai_summary,
        capability_domain="视频解析",
        covers=(
            "哔哩哔哩视频官方 AI 总结、章节大纲与视频简介",
            "支持 bilibili.com 视频链接、b23.tv 短链、BV 号和 AV 号",
        ),
        aliases=(
            "哔哩哔哩·视频AI总结",
            "B站·视频总结",
            "bilibili·视频摘要",
        ),
        visible_when=_context_mentions_bilibili,
    ),
)
