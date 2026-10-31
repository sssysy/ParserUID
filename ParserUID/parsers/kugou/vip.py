"""概念版 VIP 尝试的进程内 CD。"""

from __future__ import annotations

import time
import asyncio
from dataclasses import dataclass

from gsuid_core.logger import logger

from .models import VipAttempt
from .quality import try_claim_vip, fetch_vip_status
from .request import KugouRequest

VIP_COOLDOWN_SECONDS = 24 * 60 * 60


@dataclass(slots=True)
class _AttemptState:
    attempted_at: float
    success: bool
    message: str


_states: dict[str, _AttemptState] = {}
_lock = asyncio.Lock()


def reset_vip_cooldown() -> None:
    """重载/测试时清空进程内 CD。"""
    _states.clear()


def _state_key(request: KugouRequest) -> str:
    return request.credential.userid or request.credential.guid


async def ensure_concept_vip(
    client: object,
    request: KugouRequest,
) -> VipAttempt:
    from httpx import AsyncClient

    if not isinstance(client, AsyncClient):
        return VipAttempt(ran=False, success=False, message="HTTP 会话类型无效")
    key = _state_key(request)
    async with _lock:
        state = _states.get(key)
        now = time.time()
        if state is not None and now - state.attempted_at < VIP_COOLDOWN_SECONDS:
            return VipAttempt(ran=False, success=state.success, message="VIP 尝试仍在 24 小时 CD 内")
        try:
            vip_type, _ = await fetch_vip_status(client, request)
        except Exception as exc:
            logger.warning(f"[Parser·酷狗] VIP 状态查询失败 | error_type={type(exc).__name__}")
            vip_type = 0
        if vip_type > 0:
            return VipAttempt(ran=False, success=True, message="当前已有会员")
        try:
            attempt = await try_claim_vip(client, request, current_vip_type=0)
        except Exception as exc:
            logger.warning(f"[Parser·酷狗] VIP 尝试失败 | error_type={type(exc).__name__}")
            attempt = VipAttempt(ran=True, success=False, message="领取请求失败")
        _states[key] = _AttemptState(
            attempted_at=now,
            success=attempt.success,
            message=attempt.message,
        )
        logger.info(f"[Parser·酷狗] 概念版 VIP 尝试完成 | success={attempt.success} | cooldown=86400")
        return attempt
