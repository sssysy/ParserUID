"""OneBotUID 可选集成：对触发解析的消息贴表情。"""

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
