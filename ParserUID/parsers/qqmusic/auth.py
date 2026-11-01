"""QQ音乐扫码登录、凭据解析与本地保存。"""

from __future__ import annotations

import re
import time
import asyncio
from enum import Enum
from uuid import uuid4
from typing import Final
from dataclasses import dataclass

import httpx
import msgspec

from gsuid_core.data_store import get_res_path

from .sign import hash33
from .client import WEB_UA, QqMusicClient
from .models import QqCredential, QqStoredCredential, QqStoredCredentialData
from ...utils.exceptions import ParseException

COOKIE_FILE: Final = get_res_path("ParserUID") / "cookies" / "qqmusic_credential.json"
QQ_QR_URL: Final = "https://ssl.ptlogin2.qq.com/ptqrshow"
QQ_CHECK_URL: Final = "https://ssl.ptlogin2.qq.com/ptqrlogin"
QQ_CHECK_SIG_URL: Final = "https://ssl.ptlogin2.graph.qq.com/check_sig"
QQ_AUTHORIZE_URL: Final = "https://graph.qq.com/oauth2.0/authorize"
WX_QR_PAGE_URL: Final = "https://open.weixin.qq.com/connect/qrconnect"
WX_CHECK_URL: Final = "https://lp.open.weixin.qq.com/connect/l/qrconnect"
WX_APP_ID: Final = "wx48db31d50e334801"

_QQ_STATUS_RE = re.compile(r"ptuiCB\((.*?)\)")
_QQ_ARGS_RE = re.compile(r"'((?:\\.|[^'])*)'")
_QQ_SIGX_RE = re.compile(r"(?:\?|&)ptsigx=(.+?)&s_url")
_QQ_UIN_RE = re.compile(r"(?:\?|&)uin=(.+?)&service")
_WX_UUID_RE = re.compile(r'uuid=(.+?)"')
_WX_STATUS_RE = re.compile(r"window\.wx_errcode=(\d+);window\.wx_code='([^']*)'")
_LOGIN_HEADERS = {"Referer": "https://xui.ptlogin2.qq.com/", "User-Agent": WEB_UA}


class QrKind(str, Enum):
    QQ = "qq"
    WX = "wx"


class QrState(str, Enum):
    WAITING = "waiting"
    SCANNED = "scanned"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class QrSession:
    kind: QrKind
    data: bytes
    identifier: str


async def create_qr_session(client: httpx.AsyncClient, kind: QrKind) -> QrSession:
    if kind == QrKind.WX:
        return await _create_wx_qr(client)
    return await _create_qq_qr(client)


async def check_qr_session(
    client: httpx.AsyncClient,
    session: QrSession,
) -> tuple[QrState, QqCredential | None, str]:
    if session.kind == QrKind.WX:
        return await _check_wx_qr(client, session)
    return await _check_qq_qr(client, session)


async def _create_qq_qr(client: httpx.AsyncClient) -> QrSession:
    params = {
        "appid": "716027609",
        "e": "2",
        "l": "M",
        "s": "3",
        "d": "72",
        "v": "4",
        "t": str(time.time()),
        "daid": "383",
        "pt_3rd_aid": "100497308",
    }
    try:
        response = await client.get(
            QQ_QR_URL,
            params=params,
            headers=_LOGIN_HEADERS,
            follow_redirects=False,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ParseException("QQ音乐二维码请求失败") from exc
    qrsig = response.cookies.get("qrsig")
    if not qrsig or not response.content:
        raise ParseException("QQ音乐二维码响应不完整")
    return QrSession(kind=QrKind.QQ, data=response.content, identifier=qrsig)


async def _check_qq_qr(
    client: httpx.AsyncClient,
    session: QrSession,
) -> tuple[QrState, QqCredential | None, str]:
    params = {
        "u1": "https://graph.qq.com/oauth2.0/login_jump",
        "ptqrtoken": str(hash33(session.identifier)),
        "ptredirect": "0",
        "h": "1",
        "t": "1",
        "g": "1",
        "from_ui": "1",
        "ptlang": "2052",
        "action": f"0-0-{int(time.time() * 1000)}",
        "js_ver": "20102616",
        "js_type": "1",
        "pt_uistyle": "40",
        "aid": "716027609",
        "daid": "383",
        "pt_3rd_aid": "100497308",
        "has_onekey": "1",
    }
    try:
        response = await client.get(
            QQ_CHECK_URL,
            params=params,
            headers=_LOGIN_HEADERS,
            cookies={"qrsig": session.identifier},
            follow_redirects=False,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ParseException("QQ音乐扫码状态请求失败") from exc

    status_match = _QQ_STATUS_RE.search(response.text)
    if status_match is None:
        raise ParseException("QQ音乐扫码状态响应无效")
    args = _QQ_ARGS_RE.findall(status_match.group(1))
    if not args or not args[0].isdigit():
        raise ParseException("QQ音乐扫码状态响应缺少状态码")
    status = int(args[0])
    if status == 66:
        return QrState.WAITING, None, "等待扫码中"
    if status == 67:
        return QrState.SCANNED, None, "已扫码，请在手机上确认"
    if status == 65:
        return QrState.EXPIRED, None, "二维码已过期"
    if status == 68:
        return QrState.REFUSED, None, "用户拒绝了登录"
    if status != 0 or len(args) < 3:
        raise ParseException("QQ音乐扫码返回未知状态")

    sigx_match = _QQ_SIGX_RE.findall(args[2])
    uin_match = _QQ_UIN_RE.findall(args[2])
    if not sigx_match or not uin_match:
        raise ParseException("QQ音乐扫码结果缺少授权参数")
    credential = await _authorize_qq(client, uin=uin_match[0], sigx=sigx_match[0])
    return QrState.CONFIRMED, credential, "登录成功"


async def _authorize_qq(client: httpx.AsyncClient, *, uin: str, sigx: str) -> QqCredential:
    check_params = {
        "uin": uin,
        "pttype": "1",
        "service": "ptqrlogin",
        "nodirect": "0",
        "ptsigx": sigx,
        "s_url": "https://graph.qq.com/oauth2.0/login_jump",
        "ptlang": "2052",
        "ptredirect": "100",
        "aid": "716027609",
        "daid": "383",
        "j_later": "0",
        "low_login_hour": "0",
        "regmaster": "0",
        "pt_login_type": "3",
        "pt_aid": "0",
        "pt_aaid": "16",
        "pt_light": "0",
        "pt_3rd_aid": "100497308",
    }
    try:
        check_response = await client.get(
            QQ_CHECK_SIG_URL,
            params=check_params,
            headers=_LOGIN_HEADERS,
            cookies={},
            follow_redirects=False,
        )
        check_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ParseException("QQ音乐授权票据请求失败") from exc
    p_skey = check_response.cookies.get("p_skey")
    if not p_skey:
        raise ParseException("QQ音乐授权票据缺少 p_skey")

    authorize_data = {
        "response_type": "code",
        "client_id": "100497308",
        "redirect_uri": "https://y.qq.com/portal/wx_redirect.html?login_type=1&surl=https://y.qq.com/",
        "scope": "get_user_info,get_app_friends",
        "state": "state",
        "switch": "",
        "from_ptlogin": "1",
        "src": "1",
        "update_auth": "1",
        "openapi": "1010_1030",
        "g_tk": str(hash33(p_skey, 5381)),
        "auth_time": str(int(time.time()) * 1000),
        "ui": str(uuid4()),
    }
    try:
        authorize_response = await client.post(
            QQ_AUTHORIZE_URL,
            data=authorize_data,
            headers=_LOGIN_HEADERS,
            cookies=dict(check_response.cookies),
            follow_redirects=False,
        )
        authorize_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ParseException("QQ音乐授权请求失败") from exc
    location = authorize_response.headers.get("Location", "")
    code_matches = re.findall(r"(?<=code=)(.+?)(?=&)", location)
    if not code_matches and authorize_response.text:
        code_matches = re.findall(r"(?<=code=)(.+?)(?=&|$)", authorize_response.text)
    if not code_matches:
        raise ParseException("QQ音乐授权未返回登录 code")
    return await QqMusicClient(client).login_qq(code_matches[0])


async def _create_wx_qr(client: httpx.AsyncClient) -> QrSession:
    params = {
        "appid": WX_APP_ID,
        "redirect_uri": "https://y.qq.com/portal/wx_redirect.html?login_type=2&surl=https://y.qq.com/",
        "response_type": "code",
        "scope": "snsapi_login",
        "state": "STATE",
        "href": "https://y.qq.com/mediastyle/music_v17/src/css/popup_wechat.css#wechat_redirect",
    }
    try:
        response = await client.get(WX_QR_PAGE_URL, params=params, cookies={}, follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ParseException("QQ音乐微信二维码请求失败") from exc
    matches = _WX_UUID_RE.findall(response.text)
    if not matches:
        raise ParseException("QQ音乐微信二维码缺少 uuid")
    uuid = matches[0]
    try:
        qr_response = await client.get(
            f"https://open.weixin.qq.com/connect/qrcode/{uuid}",
            headers={"Referer": "https://open.weixin.qq.com/connect/qrconnect", "User-Agent": WEB_UA},
            cookies={},
            follow_redirects=False,
        )
        qr_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ParseException("QQ音乐微信二维码图片请求失败") from exc
    if not qr_response.content:
        raise ParseException("QQ音乐微信二维码图片为空")
    return QrSession(kind=QrKind.WX, data=qr_response.content, identifier=uuid)


async def _check_wx_qr(
    client: httpx.AsyncClient,
    session: QrSession,
) -> tuple[QrState, QqCredential | None, str]:
    params = {"uuid": session.identifier, "_": str(int(time.time()) * 1000)}
    try:
        response = await client.get(
            WX_CHECK_URL,
            params=params,
            headers={"Referer": "https://open.weixin.qq.com/", "User-Agent": WEB_UA},
            follow_redirects=True,
            timeout=35.0,
        )
        response.raise_for_status()
    except httpx.TimeoutException:
        return QrState.WAITING, None, "等待扫码中"
    except httpx.HTTPError as exc:
        raise ParseException("QQ音乐微信扫码状态请求失败") from exc

    status_match = _WX_STATUS_RE.search(response.text)
    if status_match is None:
        raise ParseException("QQ音乐微信扫码状态响应无效")
    status = int(status_match.group(1))
    if status == 408:
        return QrState.WAITING, None, "等待扫码中"
    if status == 404:
        return QrState.SCANNED, None, "已扫码，请在手机上确认"
    if status == 402:
        return QrState.EXPIRED, None, "二维码已过期"
    if status == 403:
        return QrState.REFUSED, None, "用户拒绝了登录"
    if status != 405:
        raise ParseException("QQ音乐微信扫码返回未知状态")
    code = status_match.group(2)
    if not code:
        raise ParseException("QQ音乐微信登录缺少授权 code")
    credential = await QqMusicClient(client).login_wx(code)
    return QrState.CONFIRMED, credential, "登录成功"


def credential_from_config(raw: str) -> QqCredential | None:
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            payload = text.encode("utf-8")
            stored = msgspec.json.decode(payload, type=QqStoredCredential)
            wrapped = QqCredential.from_stored(stored.credential)
            if wrapped.is_valid:
                return wrapped
        except (msgspec.DecodeError, UnicodeEncodeError):
            pass
        try:
            direct = msgspec.json.decode(text.encode("utf-8"), type=QqStoredCredentialData)
        except (msgspec.DecodeError, UnicodeEncodeError):
            return None
        credential = QqCredential.from_stored(direct)
        return credential if credential.is_valid else None

    values: dict[str, str] = {}
    for part in text.replace("\n", ";").split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name.strip():
            values[name.strip()] = value.strip()
    musicid_text = _cookie_value(values, "musicid", "musicId", "uin", "qq")
    musickey = _cookie_value(values, "musickey", "music_key", "qm_keyst", "authst")
    if not musicid_text or not musickey:
        return None
    try:
        musicid = int(musicid_text.lstrip("o"))
    except ValueError:
        return None
    if musicid <= 0:
        return None
    login_type_text = _cookie_value(values, "login_type", "loginType")
    try:
        login_type = int(login_type_text) if login_type_text else _infer_login_type(musickey)
    except ValueError:
        login_type = _infer_login_type(musickey)
    return QqCredential(
        musicid=musicid,
        musickey=musickey,
        login_type=login_type,
        openid=_cookie_value(values, "openid"),
        refresh_token=_cookie_value(values, "refresh_token", "refreshToken"),
        refresh_key=_cookie_value(values, "refresh_key", "refreshKey"),
        access_token=_cookie_value(values, "access_token", "accessToken"),
        unionid=_cookie_value(values, "unionid"),
        str_musicid=_cookie_value(values, "str_musicid") or str(musicid),
    )


async def load_credential() -> QqCredential | None:
    if not await asyncio.to_thread(COOKIE_FILE.is_file):
        return None
    try:
        raw = await asyncio.to_thread(COOKIE_FILE.read_bytes)
        stored = msgspec.json.decode(raw, type=QqStoredCredential)
    except (OSError, msgspec.DecodeError):
        return None
    credential = QqCredential.from_stored(stored.credential)
    return credential if credential.is_valid else None


async def save_credential(credential: QqCredential) -> None:
    payload = QqStoredCredential(
        schema_version=1,
        saved_at=int(time.time()),
        credential=credential.to_stored(),
    )
    content = msgspec.json.encode(payload)
    await asyncio.to_thread(_write_credential, content)


def _write_credential(content: bytes) -> None:
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = COOKIE_FILE.with_name(f".{COOKIE_FILE.name}.tmp")
    temporary.write_bytes(content)
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(COOKIE_FILE)


async def remove_credential() -> None:
    await asyncio.to_thread(COOKIE_FILE.unlink, missing_ok=True)


def saved_credential_description(credential: QqCredential) -> str:
    prefix = "微信" if credential.login_type == 1 else "QQ"
    expiry = ""
    if credential.musickey_create_time > 0 and credential.key_expires_in > 0:
        expired_at = credential.musickey_create_time + credential.key_expires_in
        expiry = (
            "，已过期"
            if time.time() >= expired_at
            else f"，有效期至 {time.strftime('%Y-%m-%d', time.localtime(expired_at))}"
        )
    return f"{prefix}账号 {credential.display_id}{expiry}"


def _cookie_value(values: dict[str, str], *names: str) -> str:
    for name in names:
        if name in values:
            return values[name]
    return ""


def _infer_login_type(musickey: str) -> int:
    return 1 if musickey.startswith("W_X_") else 2
