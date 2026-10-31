"""ParserUID Web 控制台页面注册与 API 路由。"""

from __future__ import annotations

import json
import asyncio
from typing import Final, TypedDict
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

from gsuid_core.logger import logger
from gsuid_core.data_store import get_res_path
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
    ParserManifest,
    ParserFieldValue,
    ParserItemConfig,
    load_parser_user_config,
    save_parser_user_config,
)
from .utils.registry import (
    _load_manifest_from_dir,
    get_builtin_parsers_dir,
    get_thirdparty_parsers_dir,
)

PAGE_DIR: Final = Path(__file__).parent / "web"
COOKIES_DIR: Final = get_res_path("ParserUID") / "cookies"
BILIBILI_CREDENTIAL_FILE: Final = "bilibili_credential.json"

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
    fields: list[ParserFieldView]
    values: dict[str, ParserFieldValue]


class ParsersListResponse(TypedDict):
    parsers: list[ParserItemView]


class SkinItemView(TypedDict):
    name: str
    display_name: str


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


class SaveSkinRequest(BaseModel):
    name: str


class SaveProxyRequest(BaseModel):
    value: str


class BilibiliCredential(TypedDict):
    cookie_header: str
    sessdata: str
    bili_jct: str
    dedeuserid: str
    mid: int
    access_token: str
    refresh_token: str
    expires_at: float


def _bilibili_credential_path() -> Path:
    return COOKIES_DIR / BILIBILI_CREDENTIAL_FILE


def _load_bilibili_credential() -> BilibiliCredential | None:
    path = _bilibili_credential_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        logger.warning("[Parser·Web] 哔哩哔哩扫码凭证损坏，忽略")
        return None
    if not isinstance(raw, dict):
        return None

    cookie_parts: list[str] = []
    sessdata = str(raw.get("sessdata") or "")
    bili_jct = str(raw.get("bili_jct") or "")
    dedeuserid = str(raw.get("dedeuserid") or "")
    if sessdata:
        cookie_parts.append(f"SESSDATA={sessdata}")
    if bili_jct:
        cookie_parts.append(f"bili_jct={bili_jct}")
    if dedeuserid:
        cookie_parts.append(f"DedeUserID={dedeuserid}")

    if not cookie_parts:
        return None

    raw_mid = raw.get("mid")
    raw_expires = raw.get("expires_at")
    mid = int(raw_mid) if isinstance(raw_mid, (int, float, str)) and str(raw_mid).isdigit() else 0
    expires_at = float(raw_expires) if isinstance(raw_expires, (int, float)) else 0.0

    return {
        "cookie_header": "; ".join(cookie_parts),
        "sessdata": sessdata,
        "bili_jct": bili_jct,
        "dedeuserid": dedeuserid,
        "mid": mid,
        "access_token": str(raw.get("access_token") or ""),
        "refresh_token": str(raw.get("refresh_token") or ""),
        "expires_at": expires_at,
    }


def _save_bilibili_credential_if_valid(cookie_header: str) -> bool:
    if not cookie_header.strip():
        return False
    values: dict[str, str] = {}
    for item in cookie_header.replace("\n", "").split(";"):
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        if k.strip():
            values[k.strip()] = v.strip()
    if "SESSDATA" not in values:
        return False

    path = _bilibili_credential_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, object] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                current = loaded
        except (OSError, json.JSONDecodeError):
            current = {}

    payload = {
        "sessdata": values.get("SESSDATA", ""),
        "bili_jct": values.get("bili_jct", ""),
        "dedeuserid": values.get("DedeUserID", ""),
        "mid": int(current.get("mid") or 0) if isinstance(current.get("mid"), (int, float)) else 0,
        "access_token": str(current.get("access_token") or ""),
        "refresh_token": str(current.get("refresh_token") or ""),
        "expires_at": float(current.get("expires_at") or 0)
        if isinstance(current.get("expires_at"), (int, float))
        else 0.0,
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(content, encoding="utf-8")
    return True


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
    credential = await asyncio.to_thread(_load_bilibili_credential)

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

        if manifest.name == "bilibili" and credential is not None:
            public_values["cookies"] = credential["cookie_header"]
            for f_item in fields_view:
                if f_item["key"] == "cookies":
                    f_item["configured"] = True

        result.append(
            {
                "name": manifest.name,
                "display_name": manifest.display_name,
                "enabled": user_config.enabled,
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
        if item.name == "bilibili" and "cookies" in values_to_save:
            cookies = values_to_save["cookies"]
            if isinstance(cookies, str):
                if cookies.strip():
                    await asyncio.to_thread(_save_bilibili_credential_if_valid, cookies)
                else:
                    path = _bilibili_credential_path()
                    if path.is_file():
                        path.unlink(missing_ok=True)
                del values_to_save["cookies"]

        cleaned_values: dict[str, ParserFieldValue] = {}
        for f_name, f_def in manifest.fields.items():
            if f_name in values_to_save:
                cleaned_values[f_name] = values_to_save[f_name]
            else:
                cleaned_values[f_name] = f_def.default

        item_config = ParserItemConfig(
            name=item.name,
            enabled=item.enabled,
            values=cleaned_values,
        )
        await asyncio.to_thread(save_parser_user_config, item_config)

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
    skin_root = Path(__file__).parent / "render" / "pack"
    skins: list[SkinItemView] = []
    if skin_root.is_dir():
        for d in sorted(skin_root.iterdir()):
            if d.is_dir() and not d.name.startswith((".", "_")):
                disp = "默认皮肤" if d.name == "default" else d.name
                skins.append({"name": d.name, "display_name": disp})

    if not skins:
        skins.append({"name": "default", "display_name": "默认皮肤"})

    current_skin = default_skin_pack()
    response_data: SkinsListResponse = {
        "current": current_skin,
        "skins": skins,
        "editable": True,
        "message": f"当前使用的卡片皮肤为 {current_skin}。",
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
    skin_root = Path(__file__).parent / "render" / "pack"
    valid_names: set[str] = {"default"}
    if skin_root.is_dir():
        for d in skin_root.iterdir():
            if d.is_dir() and not d.name.startswith((".", "_")):
                valid_names.add(d.name)

    if req.name not in valid_names:
        return api_fail(f"不支持的皮肤：{req.name}", status=400)

    try:
        await save_skin_pack(req.name)
    except OSError as exc:
        logger.warning(f"[Parser·Web] 皮肤设置保存失败 | error_type={type(exc).__name__}")
        return api_fail("皮肤设置保存失败", status=500)

    return api_ok({"current": req.name})
