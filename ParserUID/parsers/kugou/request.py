"""酷狗普通版/概念版统一请求协议。"""

from __future__ import annotations

import json
import time
from dataclasses import replace, dataclass
from collections.abc import Mapping

import httpx

from .auth import KugouCredential
from .crypto import ApiProfile, signature_android
from ...utils.exceptions import ParseException

DEFAULT_HOST = "https://gateway.kugou.com"
ANDROID_UA = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi"
EXTRA_HEADERS = {
    "kg-rc": "1",
    "kg-thash": "5d816a0",
    "kg-rec": "1",
    "kg-rf": "B9EDA08A64250DEFFBCADDEE00F8F25F",
}


@dataclass(frozen=True, slots=True)
class KugouRequest:
    """一次酷狗请求所需的平台、凭证与加密方式。"""

    profile: ApiProfile
    credential: KugouCredential
    signature: str = "android"

    def with_credential(self, credential: KugouCredential) -> "KugouRequest":
        return replace(self, credential=credential)


def cookie_map(credential: KugouCredential) -> dict[str, str]:
    return {
        "token": credential.token,
        "userid": credential.userid,
        "t": credential.token,
        "KugooID": credential.userid,
        "t1": credential.t1,
        "vip_type": str(credential.vip_type),
        "vip_token": credential.vip_token,
        "dfid": credential.dfid,
        "mid": credential.mid,
        "KUGOU_API_MID": credential.mid,
        "KUGOU_API_GUID": credential.guid,
        "KUGOU_API_DEV": credential.dev,
        "KUGOU_API_MAC": credential.mac,
        "KUGOU_API_WEBGL": credential.webgl,
    }


def _default_params(request: KugouRequest) -> dict[str, object]:
    credential = request.credential
    return {
        "dfid": credential.dfid or "-",
        "mid": credential.mid,
        "uuid": "-",
        "appid": request.profile.appid,
        "clientver": request.profile.clientver,
        "clienttime": int(time.time()),
        "token": credential.token,
        "userid": credential.userid,
    }


def _stringify(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, Mapping):
        return json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
    return str(value)


async def request_json(
    client: httpx.AsyncClient,
    request: KugouRequest,
    *,
    path: str,
    params: Mapping[str, object] | None = None,
    body: Mapping[str, object] | str | None = None,
    router: str | None = None,
    base_url: str = DEFAULT_HOST,
    method: str = "GET",
    signed: bool = True,
    clear_defaults: bool = False,
    headers: Mapping[str, str] | None = None,
) -> dict[str, object]:
    credential = request.credential
    values = {} if clear_defaults else _default_params(request)
    if params:
        values.update(params)
    values = {key: item for key, item in values.items() if item not in ("", None)}
    body_text = ""
    if body is not None:
        body_text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if signed:
        signature = signature_android(request.profile, values, body_text) if request.signature == "android" else ""
        if signature:
            values["signature"] = signature
    request_headers = {
        "User-Agent": ANDROID_UA,
        "dfid": credential.dfid or "-",
        "mid": credential.mid,
        "clienttime": str(values.get("clienttime", int(time.time()))),
        **EXTRA_HEADERS,
    }
    if router:
        request_headers["x-router"] = router
    header_cookie = cookie_map(credential)
    if header_cookie.get("token"):
        request_headers["Cookie"] = "; ".join(
            f"{key}={value}" for key, value in header_cookie.items() if value and key not in {"vip_type"}
        )
    if headers:
        request_headers.update(headers)
    try:
        response = await client.request(
            method,
            f"{base_url}{path}",
            params=values,
            content=body_text.encode("utf-8") if body_text else None,
            headers=request_headers,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise ParseException("酷狗接口请求失败") from exc
    except ValueError as exc:
        raise ParseException("酷狗接口返回不是 JSON") from exc
    if not isinstance(payload, dict):
        raise ParseException("酷狗接口响应结构无效")
    return {key: value for key, value in payload.items() if isinstance(key, str)}


def payload_int(payload: Mapping[str, object], key: str, default: int = 0) -> int:
    value = payload.get(key)
    if isinstance(value, int) and type(value) is not bool:
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return default


def payload_str(payload: Mapping[str, object], key: str, default: str = "") -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else default
