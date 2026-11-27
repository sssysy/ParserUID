"""解析器 AI 工具的启动、刷新与关闭编排。"""

from __future__ import annotations

import asyncio

from gsuid_core.logger import logger
from gsuid_core.server import on_core_start, on_core_shutdown

from .parser import get_runtime
from .utils.ai_registry import (
    reset_ai_tool_registry,
    wait_ai_tool_index_sync,
    ensure_ai_tools_registered,
)

_refresh_lock = asyncio.Lock()
_registered = False


async def refresh_ai_tools() -> bool:
    """按当前已启用解析器刷新 AI 工具；配置保存后可直接调用。"""
    global _registered
    async with _refresh_lock:
        runtime = await get_runtime()
        if not ensure_ai_tools_registered(runtime):
            return False
        _registered = True
        return True


@on_core_start(priority=10)
async def _initialize_ai_tools() -> None:
    registered = await refresh_ai_tools()
    if not registered:
        logger.debug("[Parser·AI工具] AI 未启用，跳过工具注册")


@on_core_shutdown
async def _close_ai_tools() -> None:
    await wait_ai_tool_index_sync()
    reset_ai_tool_registry()
