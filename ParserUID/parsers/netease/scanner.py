"""网易云扫码登录命令实现。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import httpx

from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment

from .auth import QrSession, poll_qr_login, build_qr_image, create_qr_session, remove_credential
from .parser import NeteaseParser
from ...utils.exceptions import ParseException, sanitize_error_text

_login_lock = asyncio.Lock()
_login_tasks: set[asyncio.Task[None]] = set()


def _track_task(coro: Awaitable[None]) -> None:
    task = asyncio.create_task(coro)
    _login_tasks.add(task)
    task.add_done_callback(_login_tasks.discard)


async def cancel_login_tasks() -> None:
    """插件关闭或重载时取消尚未完成的扫码轮询。"""
    tasks = tuple(_login_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def start_login(bot: Bot, *, mode: str = "pc") -> None:
    """生成二维码并交给后台轮询，命令立即返回。"""
    async with _login_lock:
        parser = await NeteaseParser.active_instance()
        owns_parser = parser is None
        if parser is None:
            parser = NeteaseParser()
        try:
            client = await parser.get_client()
            qr_type = 1 if mode == "web" else 3
            session = await create_qr_session(client, qr_type=qr_type)
            await bot.send(MessageSegment.image(build_qr_image(session.qr_url)))
            _track_task(_finish_login(bot, client, session, parser, owns_parser))
            owns_parser = False
        except ParseException as exc:
            detail = sanitize_error_text(str(exc), hide_local_paths=True)
            await bot.send(f"网易云登录失败：{detail}")
        finally:
            if owns_parser:
                await parser.close()


async def _finish_login(
    bot: Bot,
    client: httpx.AsyncClient,
    session: QrSession,
    parser: NeteaseParser,
    owns_parser: bool,
) -> None:
    try:
        _, message = await poll_qr_login(client, session)
        await bot.send(message)
    except ParseException as exc:
        detail = sanitize_error_text(str(exc), hide_local_paths=True)
        await bot.send(f"网易云扫码状态查询失败：{detail}")
    finally:
        if owns_parser:
            await parser.close()


async def logout(bot: Bot, ev: Event) -> None:
    """删除本机保存的网易云登录凭据。"""
    del ev
    await asyncio.to_thread(remove_credential)
    await bot.send("网易云登录凭据已清除")
