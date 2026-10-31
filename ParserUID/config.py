import asyncio
from typing import Final, TypedDict
from pathlib import Path
from urllib.parse import urlparse

import msgspec

from gsuid_core.models import Event
from gsuid_core.data_store import get_res_path
from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsIntConfig,
    GsStrConfig,
    GsBoolConfig,
    GsListStrConfig,
)
from gsuid_core.utils.plugins_config.gs_config import StringConfig

PLUGIN_NAME: Final = "ParserUID"
DATA_DIR: Final = get_res_path(PLUGIN_NAME)
CONFIG_PATH: Final = DATA_DIR / "config.json"
SESSION_SWITCH_PATH: Final = DATA_DIR / "session_switches.json"

DEFAULT_SKIN_PACK: Final = "default"
MAX_CARD_IMAGE_BLOCKS: Final = 9


CONFIG_DEFAULT: dict[str, GSC] = {
    "enabled": GsBoolConfig(
        title="启用解析器",
        desc="总开关。关闭后不识别链接，也不启动解析任务。",
        data=True,
    ),
    "parse_replies": GsBoolConfig(
        title="解析引用内容",
        desc="开启后，当前消息没有链接时扫描被引用消息的正文和分享卡片。",
        data=True,
    ),
    "proxy_url": GsStrConfig(
        title="代理地址",
        desc="供启用“使用代理”的解析器共用，例如 http://127.0.0.1:7890；留空表示直连。",
        data="",
        regex=r"^(|https?://[^\s]+)$",
    ),
    "default_session_enabled": GsBoolConfig(
        title="新会话默认开启",
        desc="没有单独保存开关的会话是否允许解析。会话开关优先于此值。",
        data=True,
    ),
    "whitelist": GsListStrConfig(
        title="会话白名单",
        desc="只允许这些会话解析。填写 Event.session_id；非空时优先于黑名单。",
        data=[],
    ),
    "blacklist": GsListStrConfig(
        title="会话黑名单",
        desc="禁止这些会话解析。填写 Event.session_id；白名单命中时忽略此项。",
        data=[],
    ),
    "max_concurrent_parses": GsIntConfig(
        title="最大并发解析数",
        desc="同时运行的解析任务上限，超出后等待；最小生效值为 1。",
        data=3,
        max_value=32,
    ),
    "task_timeout_seconds": GsIntConfig(
        title="单任务总超时",
        desc="从任务开始到发送结束的总时限，单位秒；最小生效值为 1。",
        data=180,
    ),
    "max_file_size_mb": GsIntConfig(
        title="单文件大小上限",
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
        desc="结果内容达到该数量时尝试合并转发，默认 3；0 表示关闭。",
        data=3,
    ),
    "default_skin_pack": GsStrConfig(
        title="默认皮肤包",
        desc="预览卡片使用的皮肤包目录名，留空时回落到 default。",
        data=DEFAULT_SKIN_PACK,
    ),
    "debounce_seconds": GsIntConfig(
        title="防抖秒数",
        desc="同一会话重复链接的忽略时长；0 表示关闭防抖。",
        data=300,
    ),
    "show_download_failure": GsBoolConfig(
        title="显示下载失败提示",
        desc="开启后，媒体下载或处理失败时发送简短原因；详细错误只写日志。",
        data=True,
    ),
    "card_image_limit": GsIntConfig(
        title="卡片正文图片上限",
        desc="单个预览卡片渲染的正文图片数量上限，超出部分只走结果媒体；0 表示不限制。",
        data=MAX_CARD_IMAGE_BLOCKS,
        max_value=30,
    ),
    "node_enabled": GsBoolConfig(
        title="启用合并转发",
        desc="允许发送层使用 node。关闭后达到阈值也直接逐条发送。",
        data=True,
    ),
    "node_supported_sessions": GsListStrConfig(
        title="支持合并转发的会话",
        desc="仅这些会话使用 node，填写 Event.session_id；留空表示全部会话可用。",
        data=[],
    ),
}

parser_config: Final = StringConfig(PLUGIN_NAME, CONFIG_PATH, CONFIG_DEFAULT)


class SessionSwitchFile(TypedDict):
    """会话开关的磁盘存储文档。"""

    schema_version: int
    sessions: dict[str, bool]


def session_key_of(event: Event) -> str:
    """返回带平台和 bot 前缀的 GsCore 会话标识。"""
    return event.session_id


class SessionSwitchStore:
    """保存在 data/ParserUID 下、按会话记录开关状态的 JSON 存储。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._sessions: dict[str, bool] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = self._path.read_bytes()
        try:
            payload = msgspec.json.decode(raw, type=SessionSwitchFile)
        except msgspec.DecodeError as exc:
            raise ValueError(f"{self._path} 包含无效的会话开关文件") from exc
        if payload["schema_version"] != 1:
            raise ValueError(f"{self._path} 的 schema_version 不受支持")
        self._sessions = dict(payload["sessions"])

    def _save(self) -> None:
        payload: SessionSwitchFile = {
            "schema_version": 1,
            "sessions": self._sessions,
        }
        encoded = msgspec.json.format(msgspec.json.encode(payload), indent=2)
        temporary = self._path.with_name(f"{self._path.name}.part")
        temporary.write_bytes(encoded)
        temporary.replace(self._path)

    def get(self, session_key: str, default: bool) -> bool:
        """读取已保存的开关；没有记录时使用调用方提供的默认值。"""
        return self._sessions[session_key] if session_key in self._sessions else default

    async def set(self, session_key: str, enabled: bool) -> None:
        """保存单个会话的开关状态。"""
        if not session_key:
            raise ValueError("会话标识不能为空")
        async with self._lock:
            if session_key in self._sessions and self._sessions[session_key] is enabled:
                return
            previous = self._sessions[session_key] if session_key in self._sessions else None
            self._sessions[session_key] = enabled
            try:
                await asyncio.to_thread(self._save)
            except OSError:
                if previous is None:
                    del self._sessions[session_key]
                else:
                    self._sessions[session_key] = previous
                raise

    async def reset(self, session_key: str) -> None:
        """删除会话覆盖值，使该会话重新跟随全局默认值。"""
        if not session_key:
            raise ValueError("会话标识不能为空")
        async with self._lock:
            if session_key not in self._sessions:
                return
            previous = self._sessions[session_key]
            del self._sessions[session_key]
            try:
                await asyncio.to_thread(self._save)
            except OSError:
                self._sessions[session_key] = previous
                raise


session_switch_store: Final = SessionSwitchStore(SESSION_SWITCH_PATH)


def is_session_enabled(event: Event) -> bool:
    """同时检查全局开关和已保存的会话开关。"""
    if not bool(parser_config.get_config("enabled").data):
        return False

    session_key = session_key_of(event)
    whitelist = parser_config.get_config("whitelist").data
    blacklist = parser_config.get_config("blacklist").data
    if whitelist:
        if session_key not in whitelist:
            return False
    elif session_key in blacklist:
        return False

    default_enabled = bool(parser_config.get_config("default_session_enabled").data)
    return session_switch_store.get(session_key, default_enabled)


def is_node_enabled_for(session_key: str) -> bool:
    """按总开关和会话白名单判断是否使用合并转发。"""
    if not bool(parser_config.get_config("node_enabled").data):
        return False
    supported = parser_config.get_config("node_supported_sessions").data
    return not supported or session_key in supported


def default_skin_pack() -> str:
    """返回去掉空白后的皮肤包名，空值回落到 default。"""
    value = str(parser_config.get_config("default_skin_pack").data).strip()
    return value or DEFAULT_SKIN_PACK


def card_image_limit() -> int:
    """返回预览卡片正文图片数量上限，0 表示不限制。"""
    value = parser_config.get_config("card_image_limit").data
    return max(0, int(value))


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


async def set_session_enabled(session_key: str, enabled: bool) -> None:
    """为一个 GsCore 会话保存显式开关状态。"""
    await session_switch_store.set(session_key, enabled)


async def reset_session_enabled(session_key: str) -> None:
    """删除单个会话的显式开关，使其跟随全局默认值。"""
    await session_switch_store.reset(session_key)
