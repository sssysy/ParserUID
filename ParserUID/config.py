import asyncio
from typing import Final, TypedDict
from urllib.parse import urlparse

import msgspec

from gsuid_core.logger import logger
from gsuid_core.data_store import get_res_path
from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsIntConfig,
    GsStrConfig,
    GsBoolConfig,
    GsTimeRConfig,
)
from gsuid_core.utils.plugins_config.gs_config import StringConfig

PLUGIN_NAME: Final = "ParserUID"
DATA_DIR: Final = get_res_path(PLUGIN_NAME)
CONFIG_PATH: Final = DATA_DIR / "config.json"
RENDER_SETTINGS_PATH: Final = DATA_DIR / "render_settings.json"

DEFAULT_SKIN_PACK: Final = "default"
MAX_CARD_IMAGE_BLOCKS: Final = 9


CONFIG_DEFAULT: dict[str, GSC] = {
    "parse_replies": GsBoolConfig(
        title="解析引用内容",
        desc="开启后，当前消息没有链接时扫描被引用消息的正文和分享卡片。",
        data=True,
    ),
    "onebot_emoji_reaction": GsBoolConfig(
        title="对 OneBotUID 触发解析时启用贴表情",
        desc="仅在通过 [OneBotUID](https://github.com/xiowo/OneBotUID) 连接至 GsCore 时生效；解析命中后对触发消息贴睁眼表情，未安装 OneBotUID 时自动跳过。",
        data=False,
    ),
    "send_render_preview": GsBoolConfig(
        title="发送渲染预览卡片",
        desc="关闭后，解析器调用 send_info() 时不再渲染和发送预览卡片。",
        data=True,
    ),
    "send_parse_result": GsBoolConfig(
        title="发送解析结果",
        desc="关闭后，解析器调用 result() 时丢弃解析结果，不再发送媒体内容。",
        data=True,
    ),
    "send_trigger_feedback": GsBoolConfig(
        title="解析触发文字反馈",
        desc="开启后，解析链接命中并进入后台解析时，发送文字反馈。",
        data=False,
    ),
    "trigger_feedback_text": GsStrConfig(
        title="解析触发反馈文本",
        desc="发送触发反馈时使用；{} 会替换为解析器的平台名称。",
        data="识别 | {}",
    ),
    "proxy_url": GsStrConfig(
        title="代理地址",
        desc="供启用“使用代理”的解析器共用，例如 http://127.0.0.1:7890；留空表示直连。",
        data="",
        regex=r"^(|https?://[^\s]+)$",
    ),
    "task_timeout_seconds": GsIntConfig(
        title="任务超时时间",
        desc="从任务开始到发送结束的总时限，单位秒；最小生效值为 1。",
        data=180,
    ),
    "max_file_size_mb": GsIntConfig(
        title="文件大小上限",
        desc="单个媒体文件的下载上限，单位 MB；最小生效值为 1。",
        data=100,
    ),
    "max_video_duration_minutes": GsIntConfig(
        title="视频时长上限",
        desc="单个视频的最大时长，单位分钟；最小生效值为 1。",
        data=30,
    ),
    "max_audio_duration_minutes": GsIntConfig(
        title="音频时长上限",
        desc="单个音频的最大时长，单位分钟；最小生效值为 1。",
        data=20,
    ),
    "node_threshold": GsIntConfig(
        title="合并转发阈值",
        desc="结果内容达到该数量时尝试合并转发，默认 3。",
        data=3,
    ),
    "debounce_seconds": GsIntConfig(
        title="防抖秒数",
        desc="同一会话重复链接的忽略时长；0 表示关闭防抖。",
        data=300,
    ),
    "cache_retention_days": GsIntConfig(
        title="缓存保留天数",
        desc="定时清理时删除超过该天数的下载和渲染缓存文件；最小生效值为 1。",
        data=1,
    ),
    "cache_cleanup_time": GsTimeRConfig(
        title="缓存清理时间",
        desc="每天自动清理缓存的时间（时，分）。",
        data=(2, 0),
    ),
}

parser_config: Final = StringConfig(PLUGIN_NAME, CONFIG_PATH, CONFIG_DEFAULT)

_LEGACY_CONFIG_KEYS: Final = (
    "enabled",
    "default_session_enabled",
    "whitelist",
    "blacklist",
    "max_concurrent_parses",
    "default_skin_pack",
    "show_download_failure",
    "card_image_limit",
    "node_enabled",
    "node_supported_sessions",
)
_SKIN_NAME_ALLOWED: Final = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class RenderSettingsFile(TypedDict):
    """Plugin Page 管理的渲染设置。"""

    schema_version: int
    skin_pack: str


def _safe_skin_name(value: str) -> str:
    skin = value.strip()
    if skin and all(char in _SKIN_NAME_ALLOWED for char in skin):
        return skin
    return DEFAULT_SKIN_PACK


def _read_render_settings() -> RenderSettingsFile | None:
    if not RENDER_SETTINGS_PATH.is_file():
        return None
    try:
        payload = msgspec.json.decode(
            RENDER_SETTINGS_PATH.read_bytes(),
            type=RenderSettingsFile,
        )
    except (OSError, msgspec.DecodeError) as exc:
        logger.warning(f"[Parser·配置] 渲染设置读取失败 | error_type={type(exc).__name__}")
        return None
    if payload["schema_version"] != 1:
        logger.warning("[Parser·配置] 渲染设置版本不受支持，已回落到默认皮肤")
        return None
    return payload


def _write_render_settings(skin_pack: str) -> None:
    payload: RenderSettingsFile = {
        "schema_version": 1,
        "skin_pack": skin_pack,
    }
    encoded = msgspec.json.format(msgspec.json.encode(payload), indent=2)
    temporary = RENDER_SETTINGS_PATH.with_name(f"{RENDER_SETTINGS_PATH.name}.part")
    RENDER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(encoded)
    temporary.replace(RENDER_SETTINGS_PATH)


def _legacy_skin_pack() -> str | None:
    if "default_skin_pack" not in parser_config.config:
        return None
    value = parser_config.config["default_skin_pack"].data
    if not isinstance(value, str) or not value.strip():
        return None
    return _safe_skin_name(value)


def _initial_skin_pack() -> str:
    settings = _read_render_settings()
    if settings is not None:
        return _safe_skin_name(settings["skin_pack"])

    legacy = _legacy_skin_pack()
    if legacy is None:
        return DEFAULT_SKIN_PACK
    try:
        _write_render_settings(legacy)
    except OSError as exc:
        logger.warning(f"[Parser·配置] 皮肤设置迁移失败 | error_type={type(exc).__name__}")
    return legacy


def _prune_legacy_config() -> None:
    changed = False
    for key in _LEGACY_CONFIG_KEYS:
        if key in parser_config.config:
            del parser_config.config[key]
            changed = True
    if changed:
        parser_config.sort_config()


_current_skin_pack = _initial_skin_pack()
_prune_legacy_config()


def default_skin_pack() -> str:
    """返回 Plugin Page 当前选择的皮肤包名。"""
    return _current_skin_pack


def card_image_limit() -> int:
    """返回固定的预览卡片正文图片数量上限。"""
    return MAX_CARD_IMAGE_BLOCKS


def render_preview_enabled() -> bool:
    """返回是否渲染并发送解析器提供的预览卡片。"""
    return bool(parser_config.get_config("send_render_preview").data)


def parse_result_enabled() -> bool:
    """返回是否发送解析器返回的结果内容。"""
    return bool(parser_config.get_config("send_parse_result").data)


def trigger_feedback_enabled() -> bool:
    """返回是否在解析触发时发送文字反馈。"""
    return bool(parser_config.get_config("send_trigger_feedback").data)


def trigger_feedback_text(platform_display_name: str) -> str:
    """按配置生成触发反馈，并将占位符替换为平台可视名称。"""
    template = str(parser_config.get_config("trigger_feedback_text").data)
    return template.replace("{}", platform_display_name)


def onebot_emoji_reaction_enabled() -> bool:
    """返回是否对 OneBotUID 触发的解析消息贴表情。"""
    return bool(parser_config.get_config("onebot_emoji_reaction").data)


def max_file_size_bytes() -> int:
    """返回单文件下载上限，单位字节。"""
    megabytes = max(1, int(parser_config.get_config("max_file_size_mb").data))
    return megabytes * 1024 * 1024


def max_video_duration_seconds() -> float:
    """返回单视频时长上限，单位秒。"""
    minutes = max(1, int(parser_config.get_config("max_video_duration_minutes").data))
    return float(minutes * 60)


def max_audio_duration_seconds() -> float:
    """返回单音频时长上限，单位秒。"""
    minutes = max(1, int(parser_config.get_config("max_audio_duration_minutes").data))
    return float(minutes * 60)


def cache_retention_seconds() -> int:
    """返回缓存保留时长，单位秒。"""
    days = max(1, int(parser_config.get_config("cache_retention_days").data))
    return days * 24 * 60 * 60


def cache_cleanup_hour_minute() -> tuple[int, int]:
    """返回已约束到合法范围内的缓存清理时、分。"""
    value = parser_config.get_config("cache_cleanup_time").data
    hour = min(23, max(0, int(value[0])))
    minute = min(59, max(0, int(value[1])))
    return hour, minute


def proxy_url() -> str | None:
    """返回校验后的全局 HTTP(S) 代理地址，空值或非法值视为直连。"""
    raw = str(parser_config.get_config("proxy_url").data).strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and not 1 <= port <= 65535:
        return None
    return raw


async def save_skin_pack(skin_pack: str) -> None:
    """保存 Plugin Page 的皮肤选择。"""
    global _current_skin_pack
    skin = _safe_skin_name(skin_pack)
    await asyncio.to_thread(_write_render_settings, skin)
    _current_skin_pack = skin
