"""哔哩哔哩解析器入口：导出解析器并注册平台命令。"""

from __future__ import annotations

import asyncio

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment

from .auth import BiliAuthError
from .parser import BilibiliParser
from ...utils.exceptions import sanitize_error_text

PARSER = BilibiliParser

sv = SV("哔哩哔哩解析器命令", pm=0, area="ALL")
_login_lock = asyncio.Lock()


async def _command_parser() -> tuple[BilibiliParser, bool]:
    active = await BilibiliParser.active_instance()
    if active is not None:
        return active, False
    return BilibiliParser(), True


@sv.on_fullmatch("blogin", block=True)
async def bilibili_login(bot: Bot, ev: Event) -> None:
    """通过 B 站 TV 端二维码登录并保存可续期凭证。"""
    del ev
    async with _login_lock:
        parser, owns_parser = await _command_parser()
        try:
            qrcode = await parser.login_with_qrcode()
            await bot.send(MessageSegment.image(qrcode))
            result = await parser.complete_qr_login()
            message = result.messages[-1] if result.messages else "哔哩哔哩登录结束"
            if result.credential is not None:
                message = f"{message}，凭证已保存"
            await bot.send(message)
        except (BiliAuthError, OSError) as exc:
            detail = sanitize_error_text(str(exc), hide_local_paths=True)
            await bot.send(f"哔哩哔哩登录失败：{detail}")
        finally:
            if owns_parser:
                await parser.close()
