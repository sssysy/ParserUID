"""OneBotUID 可选集成：贴表情与 QQ 音乐卡片发送。"""

from __future__ import annotations

import importlib
from typing import Protocol, TypedDict, runtime_checkable

from gsuid_core.logger import logger
from gsuid_core.models import Event

_ONEBOT_API_MODULES = (
    "plugins.OneBotUID.api",
    "gsuid_core.plugins.OneBotUID.api",
)
_ONEBOT_EMOJI_ID = "289"
_ONEBOT_CALL_TIMEOUT = 5.0
_ONEBOT_SEND_TIMEOUT = 20.0


class _EmojiLikeParams(TypedDict):
    message_id: str
    emoji_id: str
    set: bool


class _CallBotApi(Protocol):
    async def __call__(
        self,
        action: str,
        params: dict[str, object] | None = None,
        self_id: str = "",
        timeout: float = 30.0,
    ) -> object: ...


@runtime_checkable
class _OneBotApiModule(Protocol):
    OneBotApiError: type[Exception]
    call_bot_api: _CallBotApi


def _load_onebot_api() -> _OneBotApiModule | None:
    for module_name in _ONEBOT_API_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            logger.debug(
                f"[Parser·贴表情] OneBotUID 接口导入失败 | module={module_name} | error_type={type(exc).__name__}"
            )
            continue
        if isinstance(module, _OneBotApiModule):
            return module
        logger.warning(f"[Parser·贴表情] OneBotUID 接口缺少必要成员 | module={module_name}")
    return None


def onebot_call_api_available() -> bool:
    """仅以 OneBotUID 的 call_bot_api 能否导入作为能力判断。"""
    return _load_onebot_api() is not None


async def send_music_card(ev: Event, signed_json: str) -> bool:
    """通过 OneBotUID 发送原始签名 JSON。"""
    direct = ev.user_type == "direct"
    target_id = ev.user_id if direct else ev.group_id
    if not target_id:
        logger.warning("[Parser·音乐卡片] 当前会话缺少目标 ID，跳过卡片")
        return False

    api = _load_onebot_api()
    if api is None:
        logger.warning("[Parser·音乐卡片] OneBotUID 接口不可用，跳过卡片")
        return False

    action = "send_private_msg" if direct else "send_group_msg"
    id_key = "user_id" if direct else "group_id"
    params: dict[str, object] = {
        id_key: _id_param(target_id),
        "message": [{"type": "json", "data": {"data": signed_json}}],
    }
    try:
        response = await api.call_bot_api(
            action,
            params,
            ev.bot_self_id,
            _ONEBOT_SEND_TIMEOUT,
        )
    except api.OneBotApiError as exc:
        logger.warning(f"[Parser·音乐卡片] OneBotUID 接口调用失败，跳过卡片 | error_type={type(exc).__name__}")
        return False
    except Exception as exc:
        logger.warning(f"[Parser·音乐卡片] OneBotUID 发送异常，跳过卡片 | error_type={type(exc).__name__}")
        return False
    if response is None:
        logger.warning("[Parser·音乐卡片] OneBotUID 未返回发送结果，跳过卡片")
        return False
    logger.info("[Parser·音乐卡片] 已交给 OneBotUID 发送")
    return True


async def react_to_trigger_message(ev: Event) -> bool:
    """通过 OneBotUID 对触发解析的消息贴睁眼表情。"""
    if not ev.msg_id:
        return False

    api = _load_onebot_api()
    if api is None:
        logger.debug("[Parser·贴表情] 未安装 OneBotUID，已跳过")
        return False

    params: _EmojiLikeParams = {
        "message_id": ev.msg_id,
        "emoji_id": _ONEBOT_EMOJI_ID,
        "set": True,
    }
    try:
        await api.call_bot_api(
            "set_msg_emoji_like",
            params,
            ev.bot_self_id,
            _ONEBOT_CALL_TIMEOUT,
        )
    except api.OneBotApiError as exc:
        logger.warning(f"[Parser·贴表情] OneBotUID 接口不可用 | error_type={type(exc).__name__}")
        return False
    except Exception as exc:
        logger.warning(f"[Parser·贴表情] 贴表情失败 | error_type={type(exc).__name__}")
        return False

    logger.debug("[Parser·贴表情] 已请求 OneBotUID 贴睁眼表情")
    return True


def _id_param(value: str) -> str | int:
    return int(value) if value.isdigit() else value
