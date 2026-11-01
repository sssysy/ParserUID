"""ParserUID Web 控制台页面注册与 API 路由。"""

from __future__ import annotations

import asyncio
from typing import Final, TypedDict
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

from gsuid_core.logger import logger
from gsuid_core.webconsole.plugin_page import (
    PluginAPI,
    api_ok,
    api_fail,
    register_plugin_page,
)

from .config import (
    proxy_url,
    parser_config,
    save_skin_pack,
    default_skin_pack,
)
from .parsers.config import (
    AccessMode,
    ParserManifest,
    ParserFieldValue,
    ParserItemConfig,
    normalize_group_ids,
    normalize_access_mode,
    load_parser_user_config,
    save_parser_user_config,
)
from .utils.registry import (
    _load_manifest_from_dir,
    get_builtin_parsers_dir,
    get_thirdparty_parsers_dir,
)
from .render.registry import SkinSource, scan_skin_packs

PAGE_DIR: Final = Path(__file__).parent / "web"

register_plugin_page(
    title="解析器设置",
    page_id="settings",
    static_dir=PAGE_DIR,
    description="管理各平台解析器开关、配置与皮肤包",
    title_i18n={
        "zh-CN": "解析器设置",
        "en-US": "Parser Settings",
        "ja-JP": "パーサー設定",
    },
    description_i18n={
        "zh-CN": "管理各平台解析器开关、配置与皮肤包",
        "en-US": "Manage platform parsers, settings and skins",
        "ja-JP": "各プラットフォームのパーサー、設定、スキンを管理します",
    },
)

api: Final = PluginAPI()


class ParserFieldView(TypedDict):
    key: str
    type: str
    label: str
    hint: str | None
    options: list[str]
    secret: bool
    default: ParserFieldValue
    configured: bool | None


class ParserItemView(TypedDict):
    name: str
    display_name: str
    enabled: bool
    access_mode: AccessMode
    whitelist: list[str]
    blacklist: list[str]
    fields: list[ParserFieldView]
    values: dict[str, ParserFieldValue]


class ParsersListResponse(TypedDict):
    parsers: list[ParserItemView]


class SkinItemView(TypedDict):
    name: str
    display_name: str
    version: str
    source: SkinSource


class SkinsListResponse(TypedDict):
    current: str
    skins: list[SkinItemView]
    editable: bool
    message: str


class ParserUpdatePayload(BaseModel):
    name: str
    enabled: bool
    values: dict[str, ParserFieldValue]


class SaveParsersRequest(BaseModel):
    parsers: list[ParserUpdatePayload]


class AccessListUpdatePayload(BaseModel):
    name: str
    access_mode: AccessMode
    whitelist: list[str]
    blacklist: list[str]


class SaveAccessListsRequest(BaseModel):
    parsers: list[AccessListUpdatePayload]


class SaveSkinRequest(BaseModel):
    name: str
    confirmed: bool = False


class SaveProxyRequest(BaseModel):
    value: str


def _scan_all_manifests() -> dict[str, ParserManifest]:
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


async def _reload_runtime_safely() -> None:
    from .parser import (
        ParserRuntime,
        close_runtime,
        configure_runtime,
        load_default_registry,
    )

    await close_runtime()
    new_reg = await load_default_registry()
    configure_runtime(ParserRuntime(new_reg))


@api.get("/parser-settings/parsers")
async def list_parsers() -> object:
    """返回所有解析器的元信息、字段定义与当前配置。"""
    manifests = await asyncio.to_thread(_scan_all_manifests)

    result: list[ParserItemView] = []
    for manifest in manifests.values():
        user_config = await asyncio.to_thread(load_parser_user_config, manifest)
        fields_view: list[ParserFieldView] = []
        public_values: dict[str, ParserFieldValue] = {}

        for key, field_def in manifest.fields.items():
            saved_val = user_config.values.get(key, field_def.default)
            public_values[key] = saved_val
            is_configured: bool | None = bool(saved_val) if field_def.secret else None
            fields_view.append(
                {
                    "key": key,
                    "type": field_def.type,
                    "label": field_def.label,
                    "hint": field_def.hint,
                    "options": list(field_def.options),
                    "secret": field_def.secret,
                    "default": field_def.default,
                    "configured": is_configured,
                }
            )

        result.append(
            {
                "name": manifest.name,
                "display_name": manifest.display_name,
                "enabled": user_config.enabled,
                "access_mode": user_config.access_mode,
                "whitelist": user_config.whitelist,
                "blacklist": user_config.blacklist,
                "fields": fields_view,
                "values": public_values,
            }
        )

    response_data: ParsersListResponse = {"parsers": result}
    return api_ok(response_data)


@api.post("/parser-settings/parsers")
async def save_parsers(req: SaveParsersRequest) -> object:
    """批量保存解析器设置。"""
    manifests = await asyncio.to_thread(_scan_all_manifests)

    for item in req.parsers:
        manifest = manifests.get(item.name)
        if manifest is None:
            return api_fail(f"未找到解析器: {item.name}", status=404)

        values_to_save = dict(item.values)
        cleaned_values: dict[str, ParserFieldValue] = {}
        for f_name, f_def in manifest.fields.items():
            if f_name in values_to_save:
                cleaned_values[f_name] = values_to_save[f_name]
            else:
                cleaned_values[f_name] = f_def.default

        existing = await asyncio.to_thread(load_parser_user_config, manifest)
        item_config = ParserItemConfig(
            name=item.name,
            enabled=item.enabled,
            values=cleaned_values,
            access_mode=existing.access_mode,
            whitelist=existing.whitelist,
            blacklist=existing.blacklist,
        )
        await asyncio.to_thread(save_parser_user_config, item_config)

    await _reload_runtime_safely()
    return api_ok({"saved": True, "count": len(req.parsers)})


@api.post("/parser-settings/access-lists")
async def save_access_lists(req: SaveAccessListsRequest) -> object:
    """保存每个解析器的群聊名单并刷新运行时快照。"""
    manifests = await asyncio.to_thread(_scan_all_manifests)

    for item in req.parsers:
        manifest = manifests.get(item.name)
        if manifest is None:
            return api_fail(f"未找到解析器: {item.name}", status=404)
        existing = await asyncio.to_thread(load_parser_user_config, manifest)
        whitelist = normalize_group_ids(item.whitelist)
        blacklist = normalize_group_ids(item.blacklist)
        mode = normalize_access_mode(item.access_mode)
        updated = ParserItemConfig(
            name=item.name,
            enabled=existing.enabled,
            values=existing.values,
            access_mode=mode,
            whitelist=whitelist,
            blacklist=blacklist,
        )
        await asyncio.to_thread(save_parser_user_config, updated)

    await _reload_runtime_safely()
    return api_ok({"saved": True, "count": len(req.parsers)})


@api.post("/parser-settings/parser/{name}")
async def save_single_parser(name: str, payload: ParserUpdatePayload) -> object:
    """保存单个解析器设置。"""
    if payload.name != name:
        return api_fail("解析器名称不一致", status=400)
    req = SaveParsersRequest(parsers=[payload])
    return await save_parsers(req)


@api.get("/parser-settings/skins")
async def list_skins() -> object:
    """返回可用皮肤包及当前配置。"""
    packages = await asyncio.to_thread(scan_skin_packs)
    skins: list[SkinItemView] = [
        {
            "name": package.name,
            "display_name": package.manifest.display_name,
            "version": package.manifest.version,
            "source": package.source,
        }
        for package in packages.values()
    ]

    current_skin = default_skin_pack()
    current_available = current_skin in packages
    message = f"当前使用的卡片皮肤为 {current_skin}。"
    if not current_available:
        message = f"当前皮肤 {current_skin} 不可用，请重新选择。"
    response_data: SkinsListResponse = {
        "current": current_skin,
        "skins": skins,
        "editable": True,
        "message": message,
    }
    return api_ok(response_data)


@api.get("/parser-settings/proxy")
async def get_proxy() -> object:
    """返回全局代理状态，不回显可能包含凭据的原始地址。"""
    configured = proxy_url() is not None
    return api_ok(
        {
            "configured": configured,
            "message": "已配置代理；留空保存会改为直连。" if configured else "当前直连，未配置代理。",
        }
    )


def _validated_proxy(value: str) -> tuple[str | None, str | None]:
    raw = value.strip()
    if not raw:
        return None, None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None, "代理地址必须是 http:// 或 https:// 地址"
    try:
        port = parsed.port
    except ValueError:
        return None, "代理端口无效"
    if port is not None and not 1 <= port <= 65535:
        return None, "代理端口无效"
    return raw, None


@api.post("/parser-settings/proxy")
async def save_proxy(req: SaveProxyRequest) -> object:
    """保存全局代理地址；地址规则与运行时选择使用同一语义。"""
    value, error = _validated_proxy(req.value)
    if error is not None:
        return api_fail(error, status=400)
    updated = await asyncio.to_thread(parser_config.set_config, "proxy_url", value or "")
    if not updated:
        return api_fail("代理设置保存失败", status=500)
    return api_ok({"configured": value is not None})


@api.post("/parser-settings/skin")
async def save_skin(req: SaveSkinRequest) -> object:
    """保存并切换皮肤包。"""
    packages = await asyncio.to_thread(scan_skin_packs)
    package = packages.get(req.name)
    if package is None:
        return api_fail(f"不支持的皮肤：{req.name}", status=400)
    if package.source == "thirdparty" and not req.confirmed:
        return api_fail("启用第三方皮肤需要确认", status=400)

    try:
        await save_skin_pack(package.name)
    except OSError as exc:
        logger.warning(f"[Parser·Web] 皮肤设置保存失败 | error_type={type(exc).__name__}")
        return api_fail("皮肤设置保存失败", status=500)

    return api_ok({"current": package.name, "source": package.source})
