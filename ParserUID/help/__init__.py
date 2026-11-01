"""ParserUID 帮助命令：help.json 驱动的帮助图注册与绘制。"""

from __future__ import annotations

import json
import shutil
import asyncio
from typing import NamedTuple
from pathlib import Path

from PIL import Image

from gsuid_core.sv import SV, get_plugin_available_prefix
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.data_store import get_res_path
from gsuid_core.help.model import PluginSV, PluginHelp
from gsuid_core.help.utils import register_help
from gsuid_core.help.draw_new_plugin_help import get_new_help

from ..version import version
from ..utils.registry import (
    _load_manifest_from_dir,
    get_builtin_parsers_dir,
    get_thirdparty_parsers_dir,
)

_HELP_DIR = Path(__file__).resolve().parent
_HELP_DATA = _HELP_DIR / "help.json"
TEXTURE_PATH = _HELP_DIR / "texture2d"
ICON = _HELP_DIR.parent.parent / "ICON.png"

_BANNER_SUB_TEXT = "全功能链接自动解析插件"
_ADMIN_PM = 1

try:
    PREFIX = get_plugin_available_prefix("ParserUID")
except Exception:
    PREFIX = "psr"

parser_help_sv = SV("ParserUID帮助")


class _Textures(NamedTuple):
    """帮助图素材集合，均来自 help/texture2d。"""

    icon: Image.Image
    banner: Image.Image
    bg: Image.Image
    cag: Image.Image
    item: Image.Image
    footer: Image.Image


def _parse_sv_item(item_data: dict[str, object]) -> PluginSV | None:
    """标准化单个命令项，补齐缺省字段并收窄类型。"""
    raw_name = item_data.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    name = raw_name.strip()
    raw_desc = item_data.get("desc")
    desc = raw_desc.strip() if isinstance(raw_desc, str) and raw_desc.strip() else name
    raw_eg = item_data.get("eg")
    eg = raw_eg.strip() if isinstance(raw_eg, str) and raw_eg.strip() else name
    raw_hl = item_data.get("highlight")
    highlight = raw_hl if isinstance(raw_hl, int) else 6
    raw_ck = item_data.get("need_ck")
    need_ck = raw_ck if isinstance(raw_ck, bool) else False
    raw_sk = item_data.get("need_sk")
    need_sk = raw_sk if isinstance(raw_sk, bool) else False
    raw_admin = item_data.get("need_admin")
    need_admin = raw_admin if isinstance(raw_admin, bool) else False
    return PluginSV(
        name=name,
        desc=desc,
        eg=eg,
        highlight=highlight,
        need_ck=need_ck,
        need_sk=need_sk,
        need_admin=need_admin,
    )


def sync_icon_path() -> Path:
    """汇总插件核心与各解析器的图标到 data/ParserUID/icon_path/。

    采用方案 3.1（清空重建）：
    - 基础图标来自 ParserUID/help/icon_path/（提供通用.png 与核心命令图标）
    - 各解析器可通过其目录下的 icon_path/ 提供自定义图标
    - 禁止解析器提供通用.png，如有则跳过并记录告警
    - 图标名冲突时，以后扫描/加载的解析器图标覆盖为准
    """
    target_dir = get_res_path("ParserUID") / "icon_path"
    target_dir.mkdir(parents=True, exist_ok=True)
    for existing in target_dir.iterdir():
        if existing.is_file():
            existing.unlink(missing_ok=True)

    final_icons: dict[str, Path] = {}

    # 1. 基础图标
    base_icon_dir = _HELP_DIR / "icon_path"
    if base_icon_dir.is_dir():
        for icon in sorted(base_icon_dir.glob("*.png")):
            final_icons[icon.name] = icon

    # 2. 内置与第三方解析器
    builtin_dir = get_builtin_parsers_dir()
    thirdparty_dir = get_thirdparty_parsers_dir()

    for is_thirdparty, root_dir in ((False, builtin_dir), (True, thirdparty_dir)):
        if not root_dir.is_dir():
            continue
        for child in sorted(root_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            parser_icon_dir = child / "icon_path"
            if not parser_icon_dir.is_dir():
                continue
            for icon in sorted(parser_icon_dir.glob("*.png")):
                if icon.name == "通用.png":
                    logger.warning(f"[Parser·帮助] 解析器 {child.name} 提供了被禁止的 '通用.png'，已被忽略")
                    continue
                if icon.name in final_icons:
                    logger.debug(
                        f"[Parser·帮助] 图标 {icon.name} 被后续解析器覆盖 | from={final_icons[icon.name]} to={icon}"
                    )
                final_icons[icon.name] = icon

    # 3. 复制到聚合目录
    for name, src in final_icons.items():
        shutil.copy2(src, target_dir / name)

    logger.info(f"[Parser·帮助] 图标聚合完成 | 共同步 {len(final_icons)} 个图标")
    return target_dir


def _load_help_data(pm: int) -> dict[str, PluginHelp]:
    """读 help.json 与各解析器 help.json，拼接分类并滤掉权限不足的条目。"""
    visible: dict[str, PluginHelp] = {}

    # 1. 加载插件通用命令
    if _HELP_DATA.is_file():
        try:
            raw_base = json.loads(_HELP_DATA.read_text(encoding="utf-8"))
            if isinstance(raw_base, dict):
                for cat_name, cat_obj in raw_base.items():
                    if not isinstance(cat_obj, dict):
                        continue
                    cat_pm = cat_obj.get("pm")
                    if isinstance(cat_pm, int) and pm > cat_pm:
                        continue
                    raw_data = cat_obj.get("data")
                    if not isinstance(raw_data, list):
                        continue
                    cat_desc = str(cat_obj.get("desc", ""))
                    items: list[PluginSV] = []
                    for raw_item in raw_data:
                        if isinstance(raw_item, dict):
                            sv = _parse_sv_item(raw_item)
                            if sv is not None and (pm <= _ADMIN_PM or not sv["need_admin"]):
                                items.append(sv)
                    if items:
                        visible[cat_name] = PluginHelp(desc=cat_desc, data=items)
        except Exception as exc:
            logger.warning(
                f"[Parser·帮助] 基础帮助数据加载失败 | error={type(exc).__name__}",
            )

    # 2. 扫描已注册各解析器的 help.json
    builtin_dir = get_builtin_parsers_dir()
    thirdparty_dir = get_thirdparty_parsers_dir()

    for is_thirdparty, root_dir in ((False, builtin_dir), (True, thirdparty_dir)):
        if not root_dir.is_dir():
            continue
        for child in sorted(root_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            help_file = child / "help.json"
            if not help_file.is_file():
                continue
            manifest = _load_manifest_from_dir(child)
            if manifest is None:
                continue
            try:
                raw_parser_help = json.loads(help_file.read_text(encoding="utf-8"))
                if not isinstance(raw_parser_help, dict):
                    continue
                raw_data = raw_parser_help.get("data")
                if not isinstance(raw_data, list):
                    continue
                items: list[PluginSV] = []
                for raw_item in raw_data:
                    if isinstance(raw_item, dict):
                        sv = _parse_sv_item(raw_item)
                        if sv is not None and (pm <= _ADMIN_PM or not sv["need_admin"]):
                            items.append(sv)
                if items:
                    title = f"{manifest.display_name}解析命令"
                    desc = f"由 {manifest.display_name} 解析器提供的命令"
                    visible[title] = PluginHelp(desc=desc, data=items)
            except Exception as exc:
                logger.warning(
                    f"[Parser·帮助] 解析器帮助加载失败 | name={child.name} | error={type(exc).__name__}",
                )

    return visible


def _load_textures() -> _Textures:
    """加载帮助图素材；PIL 解码是阻塞操作，统一放线程里。"""
    return _Textures(
        icon=Image.open(ICON).convert("RGBA"),
        banner=Image.open(TEXTURE_PATH / "banner_bg.png").convert("RGBA"),
        bg=Image.open(TEXTURE_PATH / "bg.png").convert("RGBA"),
        cag=Image.open(TEXTURE_PATH / "cag_bg.png").convert("RGBA"),
        item=Image.open(TEXTURE_PATH / "item.png").convert("RGBA"),
        footer=Image.open(TEXTURE_PATH / "footer.png").convert("RGBA"),
    )


async def get_help(pm: int) -> str | bytes:
    """绘制本插件帮助图；pm 为触发者的权限等级，返回图片字节或 base64 串。"""
    textures = await asyncio.to_thread(_load_textures)
    icon_path = await asyncio.to_thread(sync_icon_path)
    help_data = await asyncio.to_thread(_load_help_data, pm)
    return await get_new_help(
        plugin_name="ParserUID",
        plugin_info={f"v{version}": ""},
        plugin_icon=textures.icon,
        plugin_help=help_data,
        plugin_prefix=PREFIX,
        icon_path=icon_path,
        banner_bg=textures.banner,
        banner_sub_text=_BANNER_SUB_TEXT,
        help_bg=textures.bg,
        cag_bg=textures.cag,
        item_bg=textures.item,
        footer=textures.footer,
        column=3,
        pm=pm,
    )


@parser_help_sv.on_fullmatch("帮助", block=True)
async def parser_help(bot: Bot, ev: Event) -> None:
    """绘制 ParserUID 全部命令的帮助图。"""
    await bot.send(MessageSegment.image(await get_help(ev.user_pm)))


try:
    sync_icon_path()
except Exception as _sync_exc:
    logger.warning(f"[Parser·帮助] 启动初始化图标聚合失败 | error={type(_sync_exc).__name__}")

if ICON.is_file():
    register_help("ParserUID", f"{PREFIX}帮助", Image.open(ICON))
