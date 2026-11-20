"""ParserUID 备份建议路径注册。"""

from __future__ import annotations

from typing import Final, Literal, TypeAlias
from pathlib import Path

from .config import (
    DATA_DIR,
    CONFIG_PATH,
    PLUGIN_NAME,
    RENDER_SETTINGS_PATH,
)

_BackupHintKind: TypeAlias = Literal["user_data", "config", "regenerable"]
_BackupHintSpec: TypeAlias = tuple[Path, _BackupHintKind, str, bool | None]

_BACKUP_HINTS: Final[tuple[_BackupHintSpec, ...]] = (
    (DATA_DIR / "cookies", "user_data", "平台登录凭证", None),
    (DATA_DIR / "addons", "user_data", "第三方解析器与皮肤包", None),
    (DATA_DIR / "parser_config", "config", "各平台解析器配置文件", True),
    (CONFIG_PATH, "config", "ParserUID 全局配置", True),
    (RENDER_SETTINGS_PATH, "config", "Web 控制台已选皮肤设置", True),
    (DATA_DIR / "render_cache", "regenerable", "卡片渲染缓存", None),
    (DATA_DIR / "cache", "regenerable", "媒体下载缓存", None),
)


def register_backup_hints() -> None:
    """向 GsCore 注册备份建议路径；旧版 Core 缺失该接口时跳过。"""
    try:
        from gsuid_core.utils.backup.hints import register_backup_path
    except ImportError:
        return

    for path, kind, desc, default_selected in _BACKUP_HINTS:
        register_backup_path(PLUGIN_NAME, path, kind, desc, default_selected)
