"""自动链接解析入口与后台任务编排。"""

from __future__ import annotations

import re
import html
import time
import asyncio
import hashlib
from typing import TYPE_CHECKING, Protocol
from dataclasses import field, dataclass
from urllib.parse import urlsplit, urlunsplit
from collections.abc import Callable, Sequence, Awaitable

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.server import on_core_start, on_core_shutdown

from .config import (
    THIRDPARTY_SKINS_DIR,
    THIRDPARTY_PARSERS_DIR,
    parser_config,
    trigger_feedback_text,
    trigger_feedback_enabled,
    onebot_emoji_reaction_enabled,
)
from .utils.onebot import react_to_trigger_message
from .parsers.config import ParserItemConfig
from .utils.exceptions import (
    ParseException,
    format_user_error,
    sanitize_error_text,
    format_timeout_error,
)

if TYPE_CHECKING:
    from gsuid_core.ai_core.hooks import AgentHookResult, AgentHookContext


class Description(Protocol):
    """平台解析器生成的描述卡片数据。"""


class Platform(Protocol):
    """解析器声明的平台展示信息。"""

    display_name: str


class ContentItem(Protocol):
    """发送层接收的公共内容项。"""


class ParserEntry(Protocol):
    """注册表返回的解析器实例。"""

    config: ParserItemConfig
    platform: Platform

    async def parse(
        self,
        keyword: str,
        searched: re.Match[str],
        *,
        desc_sink: DescriptionSink | None = None,
    ) -> Sequence[ContentItem]: ...


class ParserMatch(Protocol):
    """关键词与正则命中结果。"""

    keyword: str

    @property
    def searched(self) -> re.Match[str]: ...

    @property
    def parser(self) -> ParserEntry: ...


class ParserRegistry(Protocol):
    """解析器注册表接口。"""

    def match(self, url: str) -> ParserMatch | None: ...

    def get(self, name: str) -> ParserEntry | None: ...

    async def close(self) -> None: ...


DescriptionSink = Callable[[Description], Awaitable[None]]
SendDescription = Callable[[Bot, Event, Description], Awaitable[None]]
SendResult = Callable[[Bot, Event, Sequence[ContentItem]], Awaitable[bool]]

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_CARD_SEGMENT_TYPES = frozenset({"json", "xml", "share", "card", "ark", "light_app"})
_NON_CARD_MEDIA_TYPES = frozenset({"record", "video", "file", "node"})
_MESSAGE_DEDUP_SECONDS = 600.0
_AI_BLOCK_TTL_SECONDS = 60.0
_TRAILING_PUNCTUATION = ".,;:!?，。；：！？、"
_CLOSING_PAIRS = {")": "(", "]": "[", "}": "{"}
_TRAILING_CLOSERS = "〉》」』】"
_PURE_WRAP_CHARS = " \t\r\n.,;:!?，。；：！？、()[]{}<>《》「」『』【】\"'`~～"
_MAX_CARD_DEPTH = 8


@dataclass
class _ExpiringClaims:
    ttl: float
    claims: dict[str, float] = field(default_factory=dict)
    last_purge: float = 0.0

    def claim(self, key: str, now: float) -> bool:
        if self.ttl <= 0:
            return True
        if now - self.last_purge >= min(self.ttl, 30.0):
            self._purge(now)
        if key in self.claims:
            return False
        self.claims[key] = now
        return True

    def contains(self, key: str, now: float) -> bool:
        if self.ttl <= 0 or key not in self.claims:
            return False
        claimed_at = self.claims[key]
        if now - claimed_at > self.ttl:
            del self.claims[key]
            return False
        return True

    def _purge(self, now: float) -> None:
        expires_before = now - self.ttl
        for key, claimed_at in tuple(self.claims.items()):
            if claimed_at < expires_before:
                del self.claims[key]
        self.last_purge = now


def _trim_url(candidate: str) -> str:
    url = candidate
    while url:
        stripped = url.rstrip(_TRAILING_PUNCTUATION)
        if stripped != url:
            url = stripped
            continue
        last = url[-1]
        opener = _CLOSING_PAIRS.get(last)
        if opener is not None and url.count(last) > url.count(opener):
            url = url[:-1]
            continue
        if last in _TRAILING_CLOSERS:
            url = url[:-1]
            continue
        break
    return url


def _extract_url_from_text(text: str) -> str | None:
    normalized = html.unescape(text).replace("\\/", "/")
    for matched in URL_RE.finditer(normalized):
        candidate = _trim_url(matched.group(0))
        if _is_http_url(candidate):
            return candidate
    return None


def _is_http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _find_url_in_value(
    value: object,
    depth: int = 0,
    seen: set[int] | None = None,
) -> str | None:
    if depth > _MAX_CARD_DEPTH:
        return None
    if isinstance(value, str):
        return _extract_url_from_text(value)
    if not isinstance(value, (dict, list, tuple)):
        return None

    visited = seen if seen is not None else set()
    value_id = id(value)
    if value_id in visited:
        return None
    visited.add(value_id)

    if isinstance(value, dict):
        for child in value.values():
            found = _find_url_in_value(child, depth + 1, visited)
            if found is not None:
                return found
        return None
    for child in value:
        found = _find_url_in_value(child, depth + 1, visited)
        if found is not None:
            return found
    return None


def extract_first_url(ev: Event, *, include_reply: bool) -> str | None:
    """按正文、分享卡片、引用正文的顺序提取首个 HTTP(S) URL。"""
    found = _extract_url_from_text(ev.raw_text)
    if found is not None:
        return found

    for segment in ev.content:
        if segment.type == "text":
            found = _find_url_in_value(segment.data)
            if found is not None:
                return found
        elif segment.type in _CARD_SEGMENT_TYPES:
            found = _find_url_in_value(segment.data)
            if found is not None:
                return found

    if not include_reply:
        return None
    for segment in ev.content:
        if segment.type != "reply":
            continue
        found = _find_url_in_value(segment.data)
        if found is not None:
            return found
    if ev.reply is not None:
        return _extract_url_from_text(ev.reply)
    return None


def extract_match_source(ev: Event, *, include_reply: bool) -> str | None:
    """链接优先；没有链接时返回正文或允许的引用正文供关键词匹配。"""
    found = extract_first_url(ev, include_reply=include_reply)
    if found is not None:
        return found

    text = html.unescape(ev.raw_text).strip()
    if text:
        return text
    if include_reply and ev.reply is not None:
        reply_text = html.unescape(ev.reply).strip()
        if reply_text:
            return reply_text
    return None


def normalize_url(url: str) -> str:
    """规范化用于去重的 URL，移除片段与默认端口。"""
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
            return url
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = parsed.port
        if port is not None and not (
            (parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)
        ):
            host = f"{host}:{port}"
    except ValueError:
        return url
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))


def build_dedup_key(ev: Event, normalized_url: str) -> str:
    """生成包含会话、消息 ID 与规范化链接的去重键。"""
    message_id = ev.msg_id
    if not message_id:
        material = "\x1f".join((ev.raw_text, ev.reply or "", normalized_url))
        message_id = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{ev.session_id}\x1f{message_id}\x1f{normalized_url}"


def _build_event_key(ev: Event) -> str:
    """生成单条消息级去重键，用于关联解析任务与 AI 拦截钩子。"""
    message_id = ev.msg_id
    if not message_id:
        material = "\x1f".join((ev.raw_text, ev.reply or "", str(len(ev.content))))
        message_id = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{ev.session_id}\x1f{message_id}"


def _extract_raw_text(ev: Event) -> str:
    if ev.raw_text.strip():
        return ev.raw_text
    parts = [
        segment.data.strip()
        for segment in ev.content
        if segment.type == "text" and isinstance(segment.data, str) and segment.data.strip()
    ]
    return " ".join(parts)


def _has_own_media(ev: Event) -> bool:
    reply_image_ids = set(ev.reply_image_id_list)
    has_own_image = (
        any(img_id not in reply_image_ids for img_id in ev.image_id_list)
        or bool(ev.image_id and ev.image_id not in reply_image_ids)
        or bool(ev.image and not ev.image_id_list and not ev.reply_image_id_list)
    )
    if has_own_image:
        return True
    if ev.audio_id or ev.audio_id_list or ev.video_id or ev.video_id_list or ev.file or ev.node:
        return True
    for segment in ev.content:
        if segment.type in _NON_CARD_MEDIA_TYPES and segment.data:
            return True
        if segment.type == "image" and segment.data and not ev.reply_image_id_list and not ev.image_id_list:
            return True
    return False


def is_pure_parser_message(ev: Event, source: str, matched: ParserMatch) -> bool:
    """判断消息是否仅包含可解析链接、触发词或分享卡片，不含额外聊天内容。"""
    if ev.at_list or _has_own_media(ev):
        return False
    raw_text = _extract_raw_text(ev)
    normalized_raw = html.unescape(raw_text).replace("\\/", "/").strip()
    if not normalized_raw:
        return True
    token = source if _is_http_url(source) else matched.searched.group(0)
    if not token or token not in normalized_raw:
        return False
    remainder = normalized_raw.replace(token, "", 1)
    return not remainder.strip(_PURE_WRAP_CHARS)


async def _default_send_description(bot: Bot, ev: Event, description: Description) -> None:
    from .sender import send_description

    await send_description(bot, ev, description)


async def _default_send_result(
    bot: Bot,
    ev: Event,
    result: Sequence[ContentItem],
) -> bool:
    from .sender import send_result_for_event

    return await send_result_for_event(bot, ev, result)


class ParserRuntime:
    """解析任务去重、超时与生命周期管理。"""

    def __init__(
        self,
        registry: ParserRegistry,
        *,
        send_description: SendDescription | None = None,
        send_result: SendResult | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self._send_description = send_description or _default_send_description
        self._send_result = send_result or _default_send_result
        self._clock = clock
        self._message_claims = _ExpiringClaims(_MESSAGE_DEDUP_SECONDS)
        self._ai_block_claims = _ExpiringClaims(_AI_BLOCK_TTL_SECONDS)
        self._link_claims: _ExpiringClaims | None = None
        self._debounce_seconds: float | None = None
        self._timeout: float | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    async def dispatch(self, bot: Bot, ev: Event) -> bool:
        """匹配首个解析器并在后台启动任务。"""
        if self._closed:
            return False
        await self._sync_limits()
        include_reply = bool(parser_config.get_config("parse_replies").data)
        source = extract_match_source(ev, include_reply=include_reply)
        if source is None:
            return False
        matched = self.registry.match(source)
        if matched is None:
            return False

        normalized_source = normalize_url(source) if _is_http_url(source) else " ".join(source.split())
        if not matched.parser.config.allows_access(group_id=ev.group_id, user_id=ev.user_id):
            scope = "群聊" if ev.group_id else "私聊"
            is_whitelist = (
                matched.parser.config.whitelist_enabled
                if ev.group_id
                else matched.parser.config.private_whitelist_enabled
            )
            mode = "white" if is_whitelist else "black"
            logger.info(f"[Parser·编排] {scope}被名单拦截 | parser={matched.keyword} | mode={mode}")
            return False

        now = self._clock()
        if is_pure_parser_message(ev, source, matched):
            self._ai_block_claims.claim(_build_event_key(ev), now)
        if not self._message_claims.claim(build_dedup_key(ev, normalized_source), now):
            return False
        link_claims = _require_claims(self._link_claims)
        link_key = f"{ev.session_id}\x1f{normalized_source}"
        if not link_claims.claim(link_key, now):
            logger.debug(f"[Parser·编排] 链接防抖命中 | parser={matched.keyword}")
            return False

        host = urlsplit(normalized_source).hostname if _is_http_url(normalized_source) else "text"
        logger.info(f"[Parser·解析] 链接命中，开始解析 | parser={matched.keyword} | host={host or 'unknown'}")
        if trigger_feedback_enabled():
            await self._send_trigger_feedback(bot, matched.parser.platform.display_name)
        if onebot_emoji_reaction_enabled():
            reaction_task = asyncio.create_task(
                react_to_trigger_message(ev),
                name=f"ParserUID:OneBotUID-Reaction:{matched.keyword}",
            )
            self._tasks.add(reaction_task)
            reaction_task.add_done_callback(self._on_task_done)
            bot.bot._add_bg_task(reaction_task)

        task = asyncio.create_task(
            self._run_selected(bot, ev, matched),
            name=f"ParserUID:{matched.keyword}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        bot.bot._add_bg_task(task)
        return True

    def should_block_ai(self, ev: Event) -> bool:
        """判断该事件是否应拦截 Core AI 回复（仅纯解析消息返回 True）。"""
        if self._closed:
            return False
        now = self._clock()
        if self._ai_block_claims.contains(_build_event_key(ev), now):
            return True
        include_reply = bool(parser_config.get_config("parse_replies").data)
        source = extract_match_source(ev, include_reply=include_reply)
        if source is None:
            return False
        matched = self.registry.match(source)
        if matched is None:
            return False
        if not matched.parser.config.allows_access(group_id=ev.group_id, user_id=ev.user_id):
            return False
        return is_pure_parser_message(ev, source, matched)

    async def _send_trigger_feedback(self, bot: Bot, platform_display_name: str) -> None:
        try:
            await bot.send(trigger_feedback_text(platform_display_name))
        except Exception as exc:
            error_text = sanitize_error_text(str(exc))
            logger.warning(f"[Parser·反馈] 触发文字反馈发送失败 | error_type={type(exc).__name__} | error={error_text}")

    async def _sync_limits(self) -> None:
        timeout = int(parser_config.get_config("task_timeout_seconds").data)
        self._timeout = float(max(1, timeout))
        debounce = float(max(0, int(parser_config.get_config("debounce_seconds").data)))
        if self._link_claims is None or debounce != self._debounce_seconds:
            self._debounce_seconds = debounce
            self._link_claims = _ExpiringClaims(debounce)

    async def _run_selected(self, bot: Bot, ev: Event, matched: ParserMatch) -> None:
        started_at = self._clock()
        try:
            async with asyncio.timeout(_require_timeout(self._timeout)):
                await self._parse_and_send(bot, ev, matched)
        except TimeoutError:
            elapsed = self._clock() - started_at
            logger.warning(
                f"[Parser·解析] 超时 | parser={matched.keyword} | "
                f"elapsed={elapsed:.1f}s | timeout={_require_timeout(self._timeout):.1f}s"
            )
            await self._notify_error(bot, format_timeout_error(matched.parser.platform.display_name))
        except ParseException as exc:
            elapsed = self._clock() - started_at
            error_text = sanitize_error_text(str(exc))
            exc.args = (error_text,)
            exc.message = error_text
            logger.warning(
                f"[Parser·解析] 业务异常 | parser={matched.keyword} | "
                f"error_type={type(exc).__name__} | elapsed={elapsed:.1f}s | error={error_text}"
            )
            await self._notify_error(bot, format_user_error(exc, matched.parser.platform.display_name))
        except Exception as exc:
            elapsed = self._clock() - started_at
            error_text = sanitize_error_text(str(exc))
            exc.args = (error_text,)
            logger.error(
                f"[Parser·解析] 未预期异常 | parser={matched.keyword} | "
                f"error_type={type(exc).__name__} | elapsed={elapsed:.1f}s | error={error_text}",
            )
            await self._notify_error(bot, format_user_error(exc, matched.parser.platform.display_name))

    async def _parse_and_send(self, bot: Bot, ev: Event, matched: ParserMatch) -> None:
        async def desc_sink(description: Description) -> None:
            await self._send_description(bot, ev, description)

        result = await matched.parser.parse(
            matched.keyword,
            matched.searched,
            desc_sink=desc_sink,
        )
        await self._send_result(bot, ev, result)

    async def _notify_error(self, bot: Bot, message: str) -> None:
        try:
            await bot.send(message)
        except Exception as exc:
            error_text = sanitize_error_text(str(exc))
            logger.warning(f"[Parser·提示] 发送失败 | error_type={type(exc).__name__} | error={error_text}")

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(f"[Parser·任务] 未回收异常 | error_type={type(error).__name__}")

    async def close(self) -> None:
        """取消本实例的全部后台任务并关闭注册表。"""
        if self._closed:
            return
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.registry.close()


def _require_timeout(value: float | None) -> float:
    if value is None:
        raise RuntimeError("解析运行时超时时间尚未初始化")
    return value


def _require_claims(value: _ExpiringClaims | None) -> _ExpiringClaims:
    if value is None:
        raise RuntimeError("解析运行时防抖状态尚未初始化")
    return value


async def load_default_registry() -> ParserRegistry:
    """加载默认注册表；共享接口由 utils.registry 提供。"""
    from .utils.registry import load_registry

    return await load_registry()


_runtime: ParserRuntime | None = None
_runtime_lock = asyncio.Lock()


async def get_runtime() -> ParserRuntime:
    """返回已装配的运行时，首次调用时懒加载注册表。"""
    global _runtime
    if _runtime is not None:
        return _runtime
    async with _runtime_lock:
        if _runtime is None:
            _runtime = ParserRuntime(await load_default_registry())
        return _runtime


def configure_runtime(runtime: ParserRuntime | None) -> None:
    """替换全局运行时；测试与热重载装配使用。"""
    global _runtime
    _runtime = runtime


async def close_runtime() -> None:
    """关闭并清空全局运行时。"""
    global _runtime
    async with _runtime_lock:
        runtime = _runtime
        _runtime = None
    if runtime is not None:
        await runtime.close()


sv = SV("ParserUID 自动解析", pm=6, area="ALL")

_AI_HOOK_PRIORITY = 400
_ai_hook_registered = False


def _is_sv_authorized(ev: Event) -> bool:
    """校验插件与自动解析 SV 是否对当前事件开放，避免关闭插件后误拦 AI。"""
    plugin = sv.plugins
    if not plugin.enabled or not sv.enabled:
        return False
    user_pm = int(ev.user_pm)
    if user_pm > plugin.pm or user_pm > sv.pm:
        return False
    if ev.group_id in plugin.black_list or ev.user_id in plugin.black_list:
        return False
    if ev.group_id in sv.black_list or ev.user_id in sv.black_list:
        return False
    plugin_area = plugin.area
    if not (
        plugin_area in {"SV", "ALL"}
        or (ev.user_type == "group" and plugin_area == "GROUP")
        or (ev.user_type == "direct" and plugin_area == "DIRECT")
    ):
        return False
    if not (
        sv.area == "ALL"
        or plugin_area == "ALL"
        or (ev.user_type == "group" and sv.area == "GROUP")
        or (ev.user_type == "direct" and sv.area == "DIRECT")
    ):
        return False
    if plugin.white_list and plugin.white_list != [""]:
        if ev.user_id not in plugin.white_list and ev.group_id not in plugin.white_list:
            return False
    bot_black = [bot_id for bot_id in plugin.bot_black_list if bot_id]
    if bot_black and ev.bot_self_id in bot_black:
        return False
    bot_white = [bot_id for bot_id in plugin.bot_white_list if bot_id]
    if bot_white and ev.bot_self_id not in bot_white:
        return False
    if sv.white_list and sv.white_list != [""]:
        if ev.user_id not in sv.white_list and ev.group_id not in sv.white_list:
            return False
    return True


def _collect_turn_events(primary_ev: Event) -> tuple[Event, ...]:
    """读取当前 AI 收件箱批次中的全部事件；含命令回执或连发普通消息时不拦。"""
    try:
        from gsuid_core.ai_core.inbox.turn import current_turn
    except ImportError:
        return (primary_ev,)
    turn = current_turn()
    if turn is None or not turn.entries:
        return (primary_ev,)
    if any(entry.receipt is not None for entry in turn.entries):
        return ()
    return tuple(entry.event for entry in turn.entries)


async def _block_ai_if_parsed(ctx: AgentHookContext) -> AgentHookResult | None:
    if ctx.ev is None:
        return None
    events = _collect_turn_events(ctx.ev)
    if not events:
        return None
    runtime = await get_runtime()
    for event in events:
        if not _is_sv_authorized(event) or not runtime.should_block_ai(event):
            return None
    logger.info(f"[Parser·AI拦截] 纯解析消息跳过 AI 回复 | session={ctx.session_id} | count={len(events)}")
    return ctx.abort("handled_by_parser")


def _is_core_ai_enabled() -> bool:
    try:
        from gsuid_core.ai_core.configs.ai_config import ai_config
    except ImportError:
        return False
    return bool(ai_config.get_config("enable").data)


def ensure_ai_hook_registered() -> bool:
    """在 Core 启用 AI 时按需注册 H01 拦截钩子，避免关闭 AI 时加载 ai_core。"""
    global _ai_hook_registered
    if _ai_hook_registered:
        return True
    if not _is_core_ai_enabled():
        return False
    try:
        from gsuid_core.ai_core.hooks import AgentHookPoint, on_agent_hook
    except ImportError:
        return False
    on_agent_hook(AgentHookPoint.BEFORE_AI_CHAT, priority=_AI_HOOK_PRIORITY)(_block_ai_if_parsed)
    _ai_hook_registered = True
    logger.info("[Parser·AI拦截] 已注册 BEFORE_AI_CHAT 拦截钩子")
    return True


def unregister_ai_hook() -> None:
    """插件关闭或重载时摘除本模块注册的 AI 拦截钩子。"""
    global _ai_hook_registered
    if not _ai_hook_registered:
        return
    try:
        from gsuid_core.ai_core.hooks import drop_hooks_for_module
    except ImportError:
        _ai_hook_registered = False
        return
    drop_hooks_for_module(__name__)
    _ai_hook_registered = False


@sv.on_message(block=False, prefix=False)
async def on_parser_message(bot: Bot, ev: Event) -> None:
    """自动解析消息中的首个受支持链接。"""
    ensure_ai_hook_registered()
    runtime = await get_runtime()
    await runtime.dispatch(bot, ev)


@on_core_start
async def _initialize_parser_runtime() -> None:
    await asyncio.to_thread(_ensure_addons_dirs)
    await get_runtime()
    ensure_ai_hook_registered()


def _ensure_addons_dirs() -> None:
    """启动时创建第三方资源目录，方便管理员直接放入资源。"""
    THIRDPARTY_PARSERS_DIR.mkdir(parents=True, exist_ok=True)
    THIRDPARTY_SKINS_DIR.mkdir(parents=True, exist_ok=True)


@on_core_shutdown
async def _close_parser_runtime() -> None:
    unregister_ai_hook()
    await close_runtime()
    from .render.runtime import close_render_assets

    await close_render_assets()
