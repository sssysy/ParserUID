"""酷狗普通版/概念版 Cookie 解析与扫码登录。"""

from __future__ import annotations

import io
import json
import time
from enum import Enum
from typing import Literal
from pathlib import Path
from dataclasses import dataclass

import httpx
import qrcode

from gsuid_core.data_store import get_res_path

from .crypto import ApiProfile, md5_hex, new_guid, random_upper, mid_from_guid, signature_web
from ...utils.exceptions import ParseException

QR_KEY_URL = "https://login-user.kugou.com/v2/qrcode"
QR_CHECK_URL = "https://login-user.kugou.com/v2/get_userinfo_qrcode"
QR_H5_PREFIX = "https://h5.kugou.com/apps/loginQRCode/html/index.html?qrcode="
COOKIE_DIR = get_res_path("ParserUID") / "cookies"
WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
QR_HEADERS = {"User-Agent": WEB_UA, "Referer": "https://www.kugou.com/"}

CookieMode = Literal["standard", "lite", "manual"]


class QrState(str, Enum):
    WAITING = "waiting"
    SCANNED = "scanned"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class KugouCredential:
    token: str = ""
    userid: str = ""
    t1: str = ""
    vip_type: int = 0
    vip_token: str = ""
    guid: str = ""
    mid: str = ""
    dfid: str = ""
    dev: str = ""
    mac: str = "02:00:00:00:00:00"
    webgl: str = ""

    @property
    def has_login(self) -> bool:
        return bool(self.token and self.userid)

    def cookie_header(self) -> str:
        pairs = (
            ("token", self.token),
            ("userid", self.userid),
            ("t", self.token),
            ("KugooID", self.userid),
            ("t1", self.t1),
            ("vip_type", str(self.vip_type) if self.vip_type else ""),
            ("vip_token", self.vip_token),
            ("dfid", self.dfid),
            ("mid", self.mid),
            ("KUGOU_API_MID", self.mid),
            ("KUGOU_API_GUID", self.guid),
            ("KUGOU_API_DEV", self.dev),
            ("KUGOU_API_MAC", self.mac),
            ("KUGOU_API_WEBGL", self.webgl),
        )
        return "; ".join(f"{key}={value}" for key, value in pairs if value)

    def with_identity(self, *, guid: str, mid: str, dfid: str) -> "KugouCredential":
        return KugouCredential(
            token=self.token,
            userid=self.userid,
            t1=self.t1,
            vip_type=self.vip_type,
            vip_token=self.vip_token,
            guid=guid,
            mid=mid,
            dfid=dfid,
            dev=self.dev or random_upper(10),
            mac=self.mac,
            webgl=self.webgl or str(int(md5_hex(guid)[:16], 16)),
        )

    def public_values(self) -> dict[str, str]:
        return {
            "token": self.token,
            "userid": self.userid,
            "t1": self.t1,
            "vip_type": str(self.vip_type),
            "vip_token": self.vip_token,
            "guid": self.guid,
            "mid": self.mid,
            "dfid": self.dfid,
            "dev": self.dev,
            "mac": self.mac,
            "webgl": self.webgl,
        }


@dataclass(slots=True)
class QrSession:
    mode: CookieMode
    key: str
    qr_url: str


def parse_cookie_string(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in raw.replace("\n", ";").split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name.strip():
            values[name.strip()] = value.strip()
    return values


def credential_from_cookie(raw: str, *, mode: CookieMode) -> KugouCredential:
    values = parse_cookie_string(raw)
    token = values.get("token", "") or values.get("t", "")
    userid = values.get("userid", "") or values.get("KugooID", "")
    try:
        vip_type = int(values.get("vip_type", "0") or 0)
    except ValueError:
        vip_type = 0
    guid = values.get("KUGOU_API_GUID", "") or values.get("guid", "")
    dfid = values.get("dfid", "")
    mid = values.get("KUGOU_API_MID", "") or values.get("mid", values.get("kg_mid", ""))
    if not guid:
        guid = new_guid() if mode != "manual" else md5_hex(f"{token}{userid}{time.time_ns()}")
    if not mid:
        mid = mid_from_guid(guid)
    return KugouCredential(
        token=token,
        userid=userid,
        t1=values.get("t1", ""),
        vip_type=vip_type,
        vip_token=values.get("vip_token", ""),
        guid=guid,
        mid=mid,
        dfid=dfid,
        dev=values.get("KUGOU_API_DEV", "") or random_upper(10),
        mac=values.get("KUGOU_API_MAC", "02:00:00:00:00:00") or "02:00:00:00:00:00",
        webgl=values.get("KUGOU_API_WEBGL", ""),
    )


def cookie_file(mode: str) -> Path:
    return COOKIE_DIR / f"kugou_{mode}_credential.json"


def load_credential(mode: CookieMode) -> KugouCredential | None:
    if mode == "manual":
        return None
    path = cookie_file(mode)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    values = payload.get("credential")
    if not isinstance(values, dict):
        return None
    raw = parse_cookie_string("; ".join(f"{key}={value}" for key, value in values.items() if isinstance(value, str)))
    credential = credential_from_cookie("; ".join(f"{key}={value}" for key, value in raw.items()), mode=mode)
    return credential


def save_credential(mode: str, credential: KugouCredential) -> None:
    path = cookie_file(mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "mode": mode,
        "saved_at": int(time.time()),
        "credential": credential.public_values(),
    }
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)


def remove_credential(mode: str) -> None:
    cookie_file(mode).unlink(missing_ok=True)


def build_qr_image(content: str) -> bytes:
    image = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=8, border=2)
    image.add_data(content)
    image.make(fit=True)
    buffer = io.BytesIO()
    image.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return buffer.getvalue()


async def create_qr_session(
    client: httpx.AsyncClient,
    profile: ApiProfile,
    *,
    mode: CookieMode,
) -> QrSession:
    params: dict[str, object] = {
        "appid": 1014,
        "type": 1,
        "plat": 4,
        "qrcode_txt": f"https://h5.kugou.com/apps/loginQRCode/html/index.html?appid={profile.appid}&",
        "srcappid": 2919,
        "clienttime": int(time.time()),
        "clientver": profile.clientver,
        "dfid": "-",
        "mid": mid_from_guid(new_guid()),
        "uuid": "-",
    }
    params["signature"] = signature_web(params)
    try:
        response = await client.get(QR_KEY_URL, params=params, headers=QR_HEADERS)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ParseException("酷狗二维码请求失败") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    key = data.get("qrcode") if isinstance(data, dict) else None
    if not isinstance(key, str) or not key:
        raise ParseException("酷狗二维码 key 获取失败")
    return QrSession(mode=mode, key=key, qr_url=f"{QR_H5_PREFIX}{key}")


async def check_qr_session(
    client: httpx.AsyncClient,
    profile: ApiProfile,
    session: QrSession,
) -> tuple[QrState, KugouCredential | None, str]:
    params: dict[str, object] = {
        "plat": 4,
        "appid": profile.appid,
        "srcappid": 2919,
        "qrcode": session.key,
        "dev": random_upper(10),
        "clienttime": int(time.time()),
        "clientver": profile.clientver,
        "dfid": "-",
        "mid": mid_from_guid(new_guid()),
        "uuid": "-",
    }
    params["signature"] = signature_web(params)
    try:
        response = await client.get(QR_CHECK_URL, params=params, headers=QR_HEADERS)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ParseException("酷狗扫码状态请求失败") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ParseException("酷狗扫码状态响应无效")
    status = data.get("status")
    if status == 2:
        return QrState.SCANNED, None, "已扫码，请在手机上确认"
    if status == 4:
        token = data.get("token")
        userid = data.get("userid")
        if not isinstance(token, str) or not token or not isinstance(userid, int | str):
            raise ParseException("酷狗登录成功但凭证不完整")
        credential = KugouCredential(
            token=token,
            userid=str(userid),
            vip_type=int(data.get("vip_type") or 0),
            vip_token=str(data.get("vip_token") or ""),
        )
        return QrState.CONFIRMED, credential, "登录成功"
    if status == 0:
        return QrState.EXPIRED, None, "二维码已过期"
    return QrState.WAITING, None, "等待扫码中"
