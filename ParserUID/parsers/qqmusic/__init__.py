"""QQ音乐解析器入口：导出解析器并注册扫码登录命令。"""

from __future__ import annotations

import time
import asyncio
from collections.abc import Coroutine

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.server import on_core_shutdown
from gsuid_core.segment import MessageSegment

from .auth import (
    QrKind,
    QrState,
    QrSession,
    load_credential,
    save_credential,
    check_qr_session,
    create_qr_session,
    remove_credential,
    credential_from_config,
    saved_credential_description,
)
from .parser import QQMusicParser
from ...utils.exceptions import ParseException, sanitize_error_text

PARSER = QQMusicParser

sv = SV("QQ音乐解析器命令", pm=0, area="ALL")
_login_lock = asyncio.Lock()
_login_tasks: set[asyncio.Task[None]] = set()
_LOGIN_TIMEOUT_SECONDS = 180.0


@sv.on_command(("QQ音乐登录", "qqmusiclogin", "qmlogin"), block=True)
async def qqmusic_login(bot: Bot, ev: Event) -> None:
    """生成 QQ 或微信扫码登录二维码。"""
    argument = ev.text.strip().lower()
    kind = QrKind.WX if any(token in argument for token in ("wx", "wechat", "微信")) else QrKind.QQ
    await _start_login(bot, kind)


@sv.on_fullmatch(("QQ音乐状态", "qqmusicstatus", "qmstatus"), block=True)
async def qqmusic_status(bot: Bot, ev: Event) -> None:
    """显示当前 QQ音乐登录状态。"""
    del ev
    parser = await QQMusicParser.active_instance()
    owns_parser = parser is None
    if parser is None:
        parser = QQMusicParser()
    try:
        configured = parser.get_config_value("cookie").strip()
        if configured:
            credential = credential_from_config(configured)
            source = "手动 Cookie"
            if credential is None:
                await bot.send("QQ音乐手动 Cookie 配置无效")
                return
        else:
            credential = await load_credential()
            source = "扫码凭据"
        if credential is None:
            await bot.send("QQ音乐未登录；发送“QQ音乐登录”获取二维码")
            return
        auto_refresh = "已启用" if credential.refresh_key or credential.refresh_token else "缺少续期字段"
        await bot.send(
            f"QQ音乐已登录（{source}）：{saved_credential_description(credential)}\n自动续期：{auto_refresh}"
        )
    finally:
        if owns_parser:
            await parser.close()


@sv.on_fullmatch(("QQ音乐退出", "qqmusiclogout", "qmlogout"), block=True)
async def qqmusic_logout(bot: Bot, ev: Event) -> None:
    """清除本机保存的扫码登录凭据。"""
    del ev
    await remove_credential()
    await bot.send("QQ音乐扫码凭据已清除；手动 Cookie 请在网页配置中清空")


async def _start_login(bot: Bot, kind: QrKind) -> None:
    parser = await QQMusicParser.active_instance()
    owns_parser = parser is None
    if parser is None:
        parser = QQMusicParser()
    try:
        client = await parser.get_client()
        session = await create_qr_session(client, kind)
        await bot.send(MessageSegment.image(session.data))
        await bot.send("请使用手机扫码；QQ音乐登录支持“QQ音乐登录 wx”切换微信扫码")
        _track(_finish_login(bot, parser, session, owns_parser))
        owns_parser = False
    except ParseException as exc:
        detail = sanitize_error_text(str(exc), hide_local_paths=True)
        await bot.send(f"QQ音乐登录失败：{detail}")
    finally:
        if owns_parser:
            await parser.close()


async def _finish_login(bot: Bot, parser: QQMusicParser, session: QrSession, owns_parser: bool) -> None:
    try:
        async with _login_lock:
            client = await parser.get_client()
            deadline = time.monotonic() + _LOGIN_TIMEOUT_SECONDS
            announced_scan = False
            while time.monotonic() < deadline:
                state, credential, message = await check_qr_session(client, session)
                if state is QrState.CONFIRMED and credential is not None:
                    await save_credential(credential)
                    await bot.send(f"QQ音乐登录成功：{saved_credential_description(credential)}")
                    return
                if state is QrState.SCANNED and not announced_scan:
                    announced_scan = True
                    await bot.send(message)
                if state in {QrState.EXPIRED, QrState.REFUSED}:
                    await bot.send(message)
                    return
                await asyncio.sleep(2.0)
            await bot.send("QQ音乐扫码登录超时")
    except (ParseException, OSError) as exc:
        detail = sanitize_error_text(str(exc), hide_local_paths=True)
        await bot.send(f"QQ音乐扫码状态查询失败：{detail}")
    finally:
        if owns_parser:
            await parser.close()


def _track(coro: Coroutine[object, object, None]) -> None:
    task = asyncio.create_task(coro)
    _login_tasks.add(task)
    task.add_done_callback(_login_tasks.discard)


@on_core_shutdown
async def _cancel_qqmusic_login_tasks() -> None:
    tasks = tuple(_login_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
