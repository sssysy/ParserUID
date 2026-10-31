"""网易云扫码登录与凭据持久化。"""

from __future__ import annotations

import io
import json
import time
import asyncio
import secrets
from enum import Enum
from dataclasses import dataclass

import httpx
import qrcode

from gsuid_core.logger import logger
from gsuid_core.data_store import get_res_path

from ...utils.exceptions import ParseException

QR_KEY_URI = "/api/login/qrcode/unikey"
QR_CHECK_URI = "/api/login/qrcode/client/login"
QR_LOGIN_PREFIX = "https://music.163.com/login?codekey="
COOKIE_FILE = get_res_path("ParserUID") / "cookies" / "netease_credential.json"
PUBLIC_KEY_FILE = get_res_path("ParserUID") / "cookies" / "netease_xeapi_key.json"


class QrState(str, Enum):
    WAITING = "waiting"
    SCANNED = "scanned"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"


@dataclass(slots=True)
class QrSession:
    key: str
    qr_url: str
    state: QrState = QrState.WAITING


def build_qr_image(content: str) -> bytes:
    image = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=8, border=2)
    image.add_data(content)
    image.make(fit=True)
    buffer = io.BytesIO()
    image.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return buffer.getvalue()


async def create_qr_session(client: httpx.AsyncClient) -> QrSession:
    from .request import NeteaseClient

    payload: dict[str, object] = {"type": 3}
    try:
        result, _ = await NeteaseClient(client).eapi(QR_KEY_URI, payload)
    except httpx.HTTPError as exc:
        raise ParseException("网易云二维码请求失败") from exc
    code = result.get("code")
    key = result.get("unikey")
    nested = result.get("data")
    if isinstance(nested, dict):
        key = nested.get("unikey", key)
    if code != 200 or not isinstance(key, str) or not key:
        raise ParseException("网易云二维码 key 无效")
    return QrSession(key=key, qr_url=f"{QR_LOGIN_PREFIX}{key}")


async def check_qr_session(client: httpx.AsyncClient, session: QrSession) -> tuple[QrState, str]:
    from .request import NeteaseClient

    payload: dict[str, object] = {"key": session.key, "type": 3}
    try:
        result, response = await NeteaseClient(client).eapi(QR_CHECK_URI, payload)
    except httpx.HTTPError as exc:
        raise ParseException("网易云扫码状态请求失败") from exc
    code = result.get("code")
    if code == 800:
        return QrState.EXPIRED, "二维码已过期"
    if code == 801:
        return QrState.WAITING, "等待扫码中"
    if code == 802:
        return QrState.SCANNED, "已扫码，请在手机上确认"
    if code == 803:
        cookie = _extract_cookie(response.headers.get_list("set-cookie"))
        if not cookie:
            return QrState.EXPIRED, "登录成功但未收到有效 Cookie"
        save_credential(cookie)
        return QrState.CONFIRMED, "登录成功"
    return QrState.EXPIRED, "扫码状态异常"


async def poll_qr_login(
    client: httpx.AsyncClient,
    session: QrSession,
    *,
    attempts: int = 60,
    interval: float = 2.0,
) -> tuple[QrState, str]:
    state = QrState.WAITING
    message = "等待扫码"
    for _ in range(attempts):
        state, message = await check_qr_session(client, session)
        if state in (QrState.CONFIRMED, QrState.EXPIRED):
            return state, message
        await asyncio.sleep(interval)
    return QrState.EXPIRED, "二维码登录超时"


def _extract_cookie(headers: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in headers:
        first = raw.split(";", 1)[0]
        name, separator, value = first.partition("=")
        if separator and name.strip() and value.strip():
            values[name.strip()] = value.strip()
    values.setdefault("os", "pc")
    values.setdefault("deviceId", _random_device_id())
    values.setdefault("sDeviceId", values["deviceId"])
    return values


def _random_device_id() -> str:
    return "".join(secrets.choice("0123456789ABCDEF") for _ in range(52))


def save_credential(cookie: dict[str, str]) -> None:
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "cookie": cookie,
        "saved_at": int(time.time()),
    }
    temporary = COOKIE_FILE.with_name(f".{COOKIE_FILE.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(COOKIE_FILE)


def load_credential() -> dict[str, str]:
    if not COOKIE_FILE.is_file():
        return {}
    try:
        payload = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"[Parser·网易云] 登录凭据读取失败 | error_type={type(exc).__name__}")
        return {}
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("cookie")
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)}


def remove_credential() -> None:
    COOKIE_FILE.unlink(missing_ok=True)


def load_public_key_state() -> dict[str, str]:
    if not PUBLIC_KEY_FILE.is_file():
        return {}
    try:
        raw = json.loads(PUBLIC_KEY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)}


def save_public_key_state(version: str, public_key: str, sk: str) -> None:
    PUBLIC_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": version, "publicKey": public_key, "sk": sk}
    temporary = PUBLIC_KEY_FILE.with_name(f".{PUBLIC_KEY_FILE.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(PUBLIC_KEY_FILE)
