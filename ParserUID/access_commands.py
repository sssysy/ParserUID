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
AccessAction = Literal["add", "remove"]


def _split_parser_names(raw: str) -> list[str]:
    """按空白拆分解析器名，并保持顺序去重。"""
    names: list[str] = []
    for name in raw.split():
        if name not in names:
            names.append(name)
    return names


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
    return _update_target_in_lists(
        manifests,
        target_id,
        parser_name,
        kind,
        action="add",
        is_private=is_private,
    )


def _update_target_in_lists(
    manifests: dict[str, ParserManifest],
    target_id: str,
    parser_name: str,
    kind: AccessListKind,
    *,
    action: AccessAction,
    is_private: bool = False,
) -> tuple[int, str | None]:
    parser_names = _split_parser_names(parser_name)
    if not parser_names:
        targets = list(manifests.values())
    else:
        missing = [name for name in parser_names if name not in manifests]
        if missing:
            return 0, "、".join(missing)
        targets = [manifests[name] for name in parser_names]

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
                if action == "add":
                    private_whitelist = normalize_user_ids([*private_whitelist, target_id])
                    private_whitelist_enabled = True
                else:
                    private_whitelist = [item for item in private_whitelist if item != target_id]
                    if not private_whitelist:
                        private_whitelist_enabled = False
            elif action == "add":
                private_blacklist = normalize_user_ids([*private_blacklist, target_id])
            else:
                private_blacklist = [item for item in private_blacklist if item != target_id]
        else:
            if kind == "white":
                if action == "add":
                    whitelist = normalize_group_ids([*whitelist, target_id])
                    whitelist_enabled = True
                else:
                    whitelist = [item for item in whitelist if item != target_id]
                    if not whitelist:
                        whitelist_enabled = False
            elif action == "add":
                blacklist = normalize_group_ids([*blacklist, target_id])
            else:
                blacklist = [item for item in blacklist if item != target_id]

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


async def _update_access_target(
    bot: Bot,
    ev: Event,
    *,
    kind: AccessListKind,
    action: AccessAction,
) -> None:
    group_id = (ev.group_id or "").strip()
    is_private = not group_id
    target_id = (ev.user_id or "").strip() if is_private else group_id
    if not target_id:
        await bot.send("未获取到当前会话标识，无法更新名单")
        return

    parser_names = _split_parser_names(ev.text)
    parser_name = " ".join(parser_names)
    try:
        manifests = await asyncio.to_thread(_scan_manifests)
        _count, missing = await asyncio.to_thread(
            _update_target_in_lists,
            manifests,
            target_id,
            parser_name,
            kind,
            action=action,
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
    target = "、".join(parser_names) or "全部解析器"
    if action == "add":
        await bot.send(f"已将{target_label} 添加到 {target} 的{list_label}")
    else:
        await bot.send(f"已将{target_label} 从 {target} 的{list_label}中移除")


sv = SV("ParserUID 解析器名单", pm=0, area="ALL")


@sv.on_command("添加解析白名单", block=False)
async def add_parser_whitelist_group(bot: Bot, ev: Event) -> None:
    """把当前群聊或私聊用户加入指定解析器或全部解析器的白名单，多个解析器用空格分隔。"""
    await _update_access_target(bot, ev, kind="white", action="add")


@sv.on_command("添加解析黑名单", block=False)
async def add_parser_blacklist_group(bot: Bot, ev: Event) -> None:
    """把当前群聊或私聊用户加入指定解析器或全部解析器的黑名单，多个解析器用空格分隔。"""
    await _update_access_target(bot, ev, kind="black", action="add")


@sv.on_command("删除解析白名单", block=False)
async def remove_parser_whitelist_group(bot: Bot, ev: Event) -> None:
    """把当前群聊或私聊用户从指定解析器或全部解析器的白名单中移除，多个解析器用空格分隔。"""
    await _update_access_target(bot, ev, kind="white", action="remove")


@sv.on_command("删除解析黑名单", block=False)
async def remove_parser_blacklist_group(bot: Bot, ev: Event) -> None:
    """把当前群聊或私聊用户从指定解析器或全部解析器的黑名单中移除，多个解析器用空格分隔。"""
    await _update_access_target(bot, ev, kind="black", action="remove")
