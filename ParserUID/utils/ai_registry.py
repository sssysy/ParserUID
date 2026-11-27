"""解析器 AI 工具的统一注册、卸载与索引刷新。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from gsuid_core.logger import logger

from .ai_models import AiToolSpec

if TYPE_CHECKING:
    from ..parser import ParserRuntime

_registered_names: set[str] = set()
_registered_signature: frozenset[tuple[str, str]] | None = None
_sync_task: asyncio.Task[None] | None = None


def _collect_specs(runtime: "ParserRuntime") -> list[tuple[str, AiToolSpec]]:
    """按注册快照收集已启用解析器声明的 AI 工具。"""
    collected: list[tuple[str, AiToolSpec]] = []
    seen: dict[str, str] = {}
    for parser in runtime.registry.parsers():
        specs = parser.build_ai_tools()
        for spec in specs:
            name = spec.name
            if name in seen:
                logger.warning(
                    f"[Parser·AI工具] 工具名冲突被拒绝 | name={name} "
                    f"| existing={seen[name]} | candidate={parser.platform.name}",
                )
                continue
            seen[name] = parser.platform.name
            collected.append((parser.platform.name, spec))
    return collected


def _register_spec(plugin_name: str, spec: AiToolSpec) -> bool:
    """把一个解析器声明包装成 GsCore AI 工具。"""
    from gsuid_core.ai_core.register import ai_tools, find_tool_base

    if find_tool_base(spec.name) is not None:
        return False
    try:
        ai_tools(
            category=spec.category,
            capability_domain=spec.capability_domain,
            covers=list(spec.covers) or None,
            aliases=list(spec.aliases) or None,
            visible_when=spec.visible_when,
            timeout=spec.timeout,
        )(spec.func)
    except Exception as exc:
        logger.warning(
            f"[Parser·AI工具] 注册失败 | name={spec.name} | parser={plugin_name} | error_type={type(exc).__name__}",
        )
        return False
    _registered_names.add(spec.name)
    return find_tool_base(spec.name) is not None


def _unregister_registered_tools() -> None:
    from gsuid_core.ai_core.register import unregister_tool

    for name in tuple(_registered_names):
        unregister_tool(name)
    _registered_names.clear()


def _schedule_index_sync() -> None:
    """注册完成后刷新词法索引；可用时再同步向量库。"""
    global _sync_task
    if _sync_task is not None and not _sync_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _sync() -> None:
        try:
            from gsuid_core.ai_core.register import get_all_tools
            from gsuid_core.ai_core.rag.tool_lexical import rebuild_index

            tools = get_all_tools()
            await asyncio.to_thread(rebuild_index, tools)
            from gsuid_core.ai_core.rag.base import client, embedding_model

            if client is not None and embedding_model is not None:
                from gsuid_core.ai_core.rag.tools import sync_tools

                await sync_tools(tools)
        except Exception as exc:
            logger.warning(f"[Parser·AI工具] 索引同步失败 | error_type={type(exc).__name__}")

    _sync_task = loop.create_task(_sync(), name="ParserUID:AiToolIndexSync")


def ensure_ai_tools_registered(runtime: "ParserRuntime") -> bool:
    """按当前运行时快照同步解析器 AI 工具，重复调用无副作用。"""
    global _registered_signature
    from gsuid_core.ai_core.configs.ai_config import ai_config

    if not bool(ai_config.get_config("enable").data):
        return False

    specs = _collect_specs(runtime)
    signature = frozenset((plugin, spec.name) for plugin, spec in specs)
    if signature == _registered_signature:
        return True

    _unregister_registered_tools()
    registered = 0
    for plugin_name, spec in specs:
        if _register_spec(plugin_name, spec):
            registered += 1
    _registered_signature = signature
    if registered:
        logger.info(f"[Parser·AI工具] 已同步解析器工具 | count={registered} | parsers={len({p for p, _ in specs})}")
        _schedule_index_sync()
    return True


def reset_ai_tool_registry() -> None:
    """插件关闭或热重载时摘除本插件注册的工具与索引状态。"""
    global _registered_signature, _sync_task
    _unregister_registered_tools()
    _registered_signature = None
    if _sync_task is not None and not _sync_task.done():
        _sync_task.cancel()
    _sync_task = None


async def wait_ai_tool_index_sync() -> None:
    """等待后台索引同步完成，供关闭流程使用。"""
    task = _sync_task
    if task is None:
        return
    await asyncio.gather(task, return_exceptions=True)
