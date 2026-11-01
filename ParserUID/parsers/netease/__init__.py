"""网易云音乐解析器入口：导出解析器并注册扫码登录命令。"""

from __future__ import annotations

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

from .parser import NeteaseParser
from .scanner import LOGIN_COMMAND, LOGOUT_COMMAND, logout, start_login
from ...utils.exceptions import ParseException, sanitize_error_text

PARSER = NeteaseParser

sv = SV("网易云音乐解析器命令", pm=0, area="ALL")


@sv.on_command(LOGIN_COMMAND, block=True)
async def netease_login(bot: Bot, ev: Event) -> None:
    """生成网易云扫码二维码，支持 nlogin（PC 客户端模式）与 nlogin web（网页模式）。"""
    arg = ev.text.strip().lower()
    mode = "web" if "web" in arg else "pc"
    try:
        await start_login(bot, mode=mode)
    except ParseException as exc:
        detail = sanitize_error_text(str(exc), hide_local_paths=True)
        await bot.send(f"网易云登录失败：{detail}")


@sv.on_fullmatch(LOGOUT_COMMAND, block=True)
async def netease_logout(bot: Bot, ev: Event) -> None:
    """清除本机保存的网易云登录凭据。"""
    await logout(bot, ev)
