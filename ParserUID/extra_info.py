"""ParserUID 额外状态监控项注册。"""

from __future__ import annotations

import time
import asyncio
from pathlib import Path

from PIL import Image

from gsuid_core.data_store import get_res_path
from gsuid_core.status.plugin_status import register_status

from .parsers.config import ParserManifest, load_parser_user_config
from .utils.registry import (
    _load_manifest_from_dir,
    get_builtin_parsers_dir,
    get_thirdparty_parsers_dir,
)

_ICON_PATH = Path(__file__).resolve().parent.parent / "ICON.png"
_CACHE_TTL_SECONDS = 5.0

_cached_stats_time = 0.0
_cached_parser_stats: tuple[int, int] = (0, 0)
_cached_skins_time = 0.0
_cached_skins_count: int = 0


def _scan_parser_stats() -> tuple[int, int]:
    manifests: dict[str, ParserManifest] = {}
    for root_dir in (get_builtin_parsers_dir(), get_thirdparty_parsers_dir()):
        if not root_dir.is_dir():
            continue
        for child in sorted(root_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            manifest = _load_manifest_from_dir(child)
            if manifest is not None and manifest.name not in manifests:
                manifests[manifest.name] = manifest

    registered = len(manifests)
    enabled = sum(1 for m in manifests.values() if load_parser_user_config(m).enabled)
    return registered, enabled


def _scan_available_skins_count() -> int:
    builtin_skin_root = Path(__file__).resolve().parent / "render" / "pack"
    thirdparty_skin_root = get_res_path("ParserUID") / "thirdparty" / "renderskinpacks"
    skins: set[str] = set()

    for root_dir in (builtin_skin_root, thirdparty_skin_root):
        if not root_dir.is_dir():
            continue
        for child in sorted(root_dir.iterdir()):
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            manifest_file = child / "manifest.json"
            if manifest_file.is_file():
                skins.add(child.name)

    return max(len(skins), 1)


async def get_registered_parsers_count() -> int:
    """返回已注册解析器总数。"""
    global _cached_stats_time, _cached_parser_stats
    now = time.monotonic()
    if now - _cached_stats_time > _CACHE_TTL_SECONDS:
        _cached_parser_stats = await asyncio.to_thread(_scan_parser_stats)
        _cached_stats_time = now
    return _cached_parser_stats[0]


async def get_enabled_parsers_count() -> int:
    """返回当前已启用的解析器总数。"""
    global _cached_stats_time, _cached_parser_stats
    now = time.monotonic()
    if now - _cached_stats_time > _CACHE_TTL_SECONDS:
        _cached_parser_stats = await asyncio.to_thread(_scan_parser_stats)
        _cached_stats_time = now
    return _cached_parser_stats[1]


async def get_available_skins_count() -> int:
    """返回当前可用的皮肤包总数。"""
    global _cached_skins_time, _cached_skins_count
    now = time.monotonic()
    if now - _cached_skins_time > _CACHE_TTL_SECONDS:
        _cached_skins_count = await asyncio.to_thread(_scan_available_skins_count)
        _cached_skins_time = now
    return _cached_skins_count


def _load_icon() -> Image.Image:
    if _ICON_PATH.is_file():
        try:
            return Image.open(_ICON_PATH).convert("RGBA")
        except (OSError, ValueError):
            pass
    return Image.new("RGBA", (128, 128), (0, 0, 0, 0))


register_status(
    _load_icon(),
    "ParserUID",
    {
        "注册解析器数": get_registered_parsers_count,
        "启用解析器数": get_enabled_parsers_count,
        "可用皮肤包": get_available_skins_count,
    },
)
