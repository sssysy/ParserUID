"""解析器群聊名单命令。"""

from __future__ import annotations

import asyncio

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .parser import ParserRuntime, close_runtime, configure_runtime, load_default_registry
from .parsers.config import (
    AccessMode,
    ParserManifest,
    ParserItemConfig,
    normalize_group_ids,
    load_parser_user_config,
    save_parser_user_config,
)
from .utils.registry import _load_manifest_from_dir, get_builtin_parsers_dir, get_thirdparty_parsers_dir

_ACCESS_ERROR = "群聊名单更新失败，请稍后再试"
_reload_lock = asyncio.Lock()


def _scan_manifests() -> dict[str, ParserManifest]:
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
    return manifests


def _add_group_to_lists(
    manifests: dict[str, ParserManifest],
    group_id: str,
    parser_name: str,
    kind: AccessMode,
) -> tuple[int, str | None]:
    if parser_name:
        manifest = manifests.get(parser_name)
        if manifest is None:
            return 0, parser_name
        targets = [manifest]
    else:
        targets = list(manifests.values())

    for manifest in targets:
        config = load_parser_user_config(manifest)
        if kind == "white":
            whitelist = normalize_group_ids([*config.whitelist, group_id])
            blacklist = normalize_group_ids(config.blacklist)
        else:
            whitelist = normalize_group_ids(config.whitelist)
            blacklist = normalize_group_ids([*config.blacklist, group_id])
        updated = ParserItemConfig(
            name=config.name,
            enabled=config.enabled,
            values=config.values,
            access_mode=kind,
            whitelist=whitelist,
            blacklist=blacklist,
        )
        save_parser_user_config(updated)
    return len(targets), None


async def _add_group(
    bot: Bot,
    ev: Event,
    *,
    kind: AccessMode,
) -> None:
    group_id = ev.group_id
    if not group_id:
        await bot.send("请在群聊中使用该命令")
        return

    parser_name = " ".join(ev.text.split())
    try:
        manifests = await asyncio.to_thread(_scan_manifests)
        count, missing = await asyncio.to_thread(
            _add_group_to_lists,
            manifests,
            group_id,
            parser_name,
            kind,
        )
    except Exception as exc:
        logger.error(f"[Parser·名单] 命令更新失败 | error_type={type(exc).__name__}")
        await bot.send(_ACCESS_ERROR)
        return

    if missing is not None:
        await bot.send(f"未找到解析器：{missing}")
        return
    async with _reload_lock:
        await close_runtime()
        configure_runtime(ParserRuntime(await load_default_registry()))
    label = "白名单" if kind == "white" else "黑名单"
    target = parser_name or "全部解析器"
    await bot.send(f"已将群聊 {group_id} 添加到 {target} 的{label}")


sv = SV("ParserUID 解析器名单", pm=0, area="ALL")


@sv.on_command("添加解析白名单", block=False)
async def add_parser_whitelist_group(bot: Bot, ev: Event) -> None:
    """把当前群聊加入指定解析器或全部解析器的白名单。"""
    await _add_group(bot, ev, kind="white")


@sv.on_command("添加解析黑名单", block=False)
async def add_parser_blacklist_group(bot: Bot, ev: Event) -> None:
    """把当前群聊加入指定解析器或全部解析器的黑名单。"""
    await _add_group(bot, ev, kind="black")
