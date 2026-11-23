"""解析器群聊与私聊名单命令。"""

from __future__ import annotations

import asyncio
from typing import Literal

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event

from .parser import ParserRuntime, close_runtime, configure_runtime, load_default_registry
from .parsers.config import (
    ParserManifest,
    ParserItemConfig,
    normalize_user_ids,
    normalize_group_ids,
    load_parser_user_config,
    save_parser_user_config,
)
from .utils.registry import _load_manifest_from_dir, get_builtin_parsers_dir, get_thirdparty_parsers_dir

_ACCESS_ERROR = "名单更新失败，请稍后再试"
_reload_lock = asyncio.Lock()
AccessListKind = Literal["white", "black"]


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


def _add_target_to_lists(
    manifests: dict[str, ParserManifest],
    target_id: str,
    parser_name: str,
    kind: AccessListKind,
    *,
    is_private: bool = False,
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
        whitelist_enabled = config.whitelist_enabled
        whitelist = normalize_group_ids(config.whitelist)
        blacklist = normalize_group_ids(config.blacklist)
        private_whitelist_enabled = config.private_whitelist_enabled
        private_whitelist = normalize_user_ids(config.private_whitelist)
        private_blacklist = normalize_user_ids(config.private_blacklist)

        if is_private:
            if kind == "white":
                private_whitelist = normalize_user_ids([*private_whitelist, target_id])
                private_whitelist_enabled = True
            else:
                private_blacklist = normalize_user_ids([*private_blacklist, target_id])
        else:
            if kind == "white":
                whitelist = normalize_group_ids([*whitelist, target_id])
                whitelist_enabled = True
            else:
                blacklist = normalize_group_ids([*blacklist, target_id])

        updated = ParserItemConfig(
            name=config.name,
            enabled=config.enabled,
            values=config.values,
            whitelist_enabled=whitelist_enabled,
            whitelist=whitelist,
            blacklist=blacklist,
            private_whitelist_enabled=private_whitelist_enabled,
            private_whitelist=private_whitelist,
            private_blacklist=private_blacklist,
        )
        save_parser_user_config(updated)
    return len(targets), None


async def _add_access_target(
    bot: Bot,
    ev: Event,
    *,
    kind: AccessListKind,
) -> None:
    group_id = (ev.group_id or "").strip()
    is_private = not group_id
    target_id = (ev.user_id or "").strip() if is_private else group_id
    if not target_id:
        await bot.send("未获取到当前会话标识，无法更新名单")
        return

    parser_name = " ".join(ev.text.split())
    try:
        manifests = await asyncio.to_thread(_scan_manifests)
        count, missing = await asyncio.to_thread(
            _add_target_to_lists,
            manifests,
            target_id,
            parser_name,
            kind,
            is_private=is_private,
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
    scope_label = "私聊" if is_private else "群聊"
    target_label = f"私聊用户 {target_id}" if is_private else f"群聊 {target_id}"
    list_label = f"{scope_label}白名单" if kind == "white" else f"{scope_label}黑名单"
    target = parser_name or "全部解析器"
    await bot.send(f"已将{target_label} 添加到 {target} 的{list_label}")


sv = SV("ParserUID 解析器名单", pm=0, area="ALL")


@sv.on_command("添加解析白名单", block=False)
async def add_parser_whitelist_group(bot: Bot, ev: Event) -> None:
    """把当前群聊或私聊用户加入指定解析器或全部解析器的白名单。"""
    await _add_access_target(bot, ev, kind="white")


@sv.on_command("添加解析黑名单", block=False)
async def add_parser_blacklist_group(bot: Bot, ev: Event) -> None:
    """把当前群聊或私聊用户加入指定解析器或全部解析器的黑名单。"""
    await _add_access_target(bot, ev, kind="black")
