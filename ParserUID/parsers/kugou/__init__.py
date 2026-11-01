"""酷狗音乐解析器入口：导出解析器并注册平台命令。"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import replace
from collections.abc import Coroutine

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment

from .auth import (
    QrState,
    QrSession,
    KugouCredential,
    build_qr_image,
    load_credential,
    save_credential,
    check_qr_session,
    create_qr_session,
)
from .crypto import LITE_PROFILE, STANDARD_PROFILE, ApiProfile, new_guid, mid_from_guid
from .parser import KugouParser
from ...utils.exceptions import ParseException, sanitize_error_text

PARSER = KugouParser

sv = SV("酷狗音乐解析器命令", pm=0, area="ALL")
_login_lock = asyncio.Lock()
_login_tasks: set[asyncio.Task[None]] = set()


def _profile(concept: bool) -> ApiProfile:
    return LITE_PROFILE if concept else STANDARD_PROFILE


def _track(coro: object) -> None:
    if not isinstance(coro, Coroutine):
        raise TypeError("登录任务必须是协程")
    task = asyncio.create_task(coro)
    _login_tasks.add(task)
    task.add_done_callback(_login_tasks.discard)


@sv.on_fullmatch(("酷狗登录", "kglogin"), block=True)
async def kugou_login(bot: Bot, ev: Event) -> None:
    """通过酷狗官方二维码登录普通版。"""
    del ev
    await _start_login(bot, concept=False)


@sv.on_fullmatch(("酷狗概念版登录", "kglitelogin"), block=True)
async def kugou_lite_login(bot: Bot, ev: Event) -> None:
    """通过酷狗官方二维码登录概念版。"""
    del ev
    await _start_login(bot, concept=True)


async def _start_login(bot: Bot, *, concept: bool) -> None:
    async with _login_lock:
        parser = await KugouParser.active_instance()
        owns_parser = parser is None
        if parser is None:
            parser = KugouParser()
        try:
            client = await parser.get_client()
            session = await create_qr_session(client, _profile(concept), mode="lite" if concept else "standard")
            await bot.send(MessageSegment.image(build_qr_image(session.qr_url)))
            _track(_finish_login(bot, parser, session, concept, owns_parser))
            owns_parser = False
        except ParseException as exc:
            detail = sanitize_error_text(str(exc), hide_local_paths=True)
            await bot.send(f"酷狗登录失败：{detail}")
        finally:
            if owns_parser:
                await parser.close()


async def _finish_login(
    bot: Bot,
    parser: KugouParser,
    session: QrSession,
    concept: bool,
    owns_parser: bool,
) -> None:
    try:
        client = await parser.get_client()
        for _ in range(60):
            state, credential, message = await check_qr_session(client, _profile(concept), session)
            if state is QrState.CONFIRMED and credential is not None:
                guid = new_guid()
                saved = replace(
                    credential,
                    guid=guid,
                    mid=mid_from_guid(guid),
                    dev="".join(secrets.choice("1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10)),
                )
                save_credential("lite" if concept else "standard", saved)
                await _refresh_saved_device(parser, concept, saved)
                await bot.send("酷狗概念版登录成功，Cookie 已保存" if concept else "酷狗普通版登录成功，Cookie 已保存")
                return
            if state is QrState.EXPIRED:
                await bot.send(message)
                return
            await asyncio.sleep(2)
        await bot.send("酷狗扫码登录超时")
    except ParseException as exc:
        detail = sanitize_error_text(str(exc), hide_local_paths=True)
        await bot.send(f"酷狗扫码状态查询失败：{detail}")
    finally:
        if owns_parser:
            await parser.close()


async def _refresh_saved_device(parser: KugouParser, concept: bool, credential: KugouCredential) -> None:
    from .device import register_device
    from .request import KugouRequest

    profile = _profile(concept)
    request = KugouRequest(profile=profile, credential=credential)
    try:
        client = await parser.get_client()
        updated = await register_device(client, request)
        save_credential("lite" if concept else "standard", updated)
    except ParseException:
        pass


@sv.on_fullmatch(("酷狗状态", "kgstatus"), block=True)
async def kugou_status(bot: Bot, ev: Event) -> None:
    """显示两套 Cookie 的配置/文件状态。"""
    del ev
    parser = await KugouParser.active_instance()
    if parser is None:
        parser = KugouParser()
    try:
        lines = []
        for concept, key, label in (
            (False, "standard_cookie", "普通版"),
            (True, "concept_cookie", "概念版"),
        ):
            configured = bool(parser.get_config_value(key).strip())
            stored = load_credential("lite" if concept else "standard")
            lines.append(
                f"{label}：配置={'已填' if configured else '空'}，文件={'已保存' if stored is not None else '无'}"
            )
        lines.append(f"当前 API：{'概念版' if parser._active_profile().is_lite else '普通版'}")
        await bot.send("\n".join(lines))
    finally:
        if await KugouParser.active_instance() is None:
            await parser.close()
