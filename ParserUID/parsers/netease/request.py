"""网易云请求协议选择、设备上下文与响应解密。"""

from __future__ import annotations

import hmac
import time
import base64
import random
import secrets
from dataclasses import dataclass
from collections.abc import Mapping

import httpx
import msgspec

from .auth import load_credential, random_device_id, load_public_key_state, save_public_key_state
from .crypto import (
    XeapiPublicKey,
    eapi_form,
    xeapi_form,
    xeapi_sign,
    xeapi_response_decrypt,
    xeapi_decrypt_public_key,
    xeapi_public_key_from_values,
)
from ...utils.exceptions import ParseException

EAPI_HOST = "https://interfacepc.music.163.com"
WEAPI_HOST = "https://music.163.com"
XEAPI_HOST = "https://interface3.music.163.com"
API_HOST = "https://interface.music.163.com"
UA_WEB = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
)
UA_ANDROID = "NeteaseMusic/9.5.61.260802021928(9005061);Dalvik/2.1.0 (Linux; U; Android 12; HBN-AL00 Build/cd737a2.0)"
UA_PC_CLIENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Safari/537.36 Chrome/91.0.4472.164 NeteaseMusicDesktop/3.1.29.205117"
)
SIGN_KEY_VERSION = 0


@dataclass(frozen=True, slots=True)
class DeviceContext:
    os: str
    osver: str
    appver: str
    versioncode: str
    mobilename: str
    buildver: str
    resolution: str
    channel: str
    csrf: str
    device_id: str
    s_device_id: str
    music_u: str
    music_a: str
    nmtid: str
    ntes_nuid: str
    ntes_nnid: str
    wnmcid: str

    @classmethod
    def from_values(cls, values: Mapping[str, str]) -> DeviceContext:
        device_id = values.get("deviceId") or values.get("sDeviceId") or random_device_id()
        s_device_id = values.get("sDeviceId") or device_id
        now_ms = int(time.time() * 1000)
        nuid = values.get("_ntes_nuid") or secrets.token_hex(16)
        nnid = values.get("_ntes_nnid") or f"{nuid},{now_ms}"
        wnmcid_rand = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(6))
        wnmcid = values.get("WNMCID") or f"{wnmcid_rand}.{now_ms}.01.0"
        return cls(
            os=values.get("os", "pc"),
            osver=values.get("osver", "Microsoft-Windows-10-Professional-build-19045-64bit"),
            appver=values.get("appver", "3.1.17.204416"),
            versioncode=values.get("versioncode", "140"),
            mobilename=values.get("mobilename", ""),
            buildver=values.get("buildver", str(int(time.time()))[:10]),
            resolution=values.get("resolution", "1920x1080"),
            channel=values.get("channel", "netease"),
            csrf=values.get("__csrf", ""),
            device_id=device_id,
            s_device_id=s_device_id,
            music_u=values.get("MUSIC_U", ""),
            music_a=values.get("MUSIC_A", ""),
            nmtid=values.get("NMTID", ""),
            ntes_nuid=nuid,
            ntes_nnid=nnid,
            wnmcid=wnmcid,
        )

    def cookie_values(self) -> dict[str, str]:
        values = {
            "osver": self.osver,
            "deviceId": self.device_id,
            "sDeviceId": self.s_device_id,
            "os": self.os,
            "appver": self.appver,
            "versioncode": self.versioncode,
            "mobilename": self.mobilename,
            "buildver": self.buildver,
            "resolution": self.resolution,
            "__csrf": self.csrf,
            "channel": self.channel,
            "requestId": f"{int(time.time() * 1000)}_{secrets.randbelow(1000):04d}",
            "_ntes_nuid": self.ntes_nuid,
            "_ntes_nnid": self.ntes_nnid,
            "WNMCID": self.wnmcid,
            "WEVNSM": "1.0.0",
            "ntes_kaola_ad": "1",
            "__remember_me": "true",
            "MUSIC_U": self.music_u,
            "MUSIC_A": self.music_a,
            "NMTID": self.nmtid,
        }
        return {key: value for key, value in values.items() if value}

    def android(self) -> DeviceContext:
        return DeviceContext(
            os="android",
            osver="16",
            appver="9.5.61",
            versioncode=self.versioncode,
            mobilename=self.mobilename,
            buildver=self.buildver,
            resolution=self.resolution,
            channel=self.channel,
            csrf=self.csrf,
            device_id=self.device_id,
            s_device_id=self.s_device_id or self.device_id,
            music_u=self.music_u,
            music_a=self.music_a,
            nmtid=self.nmtid,
            ntes_nuid=self.ntes_nuid,
            ntes_nnid=self.ntes_nnid,
            wnmcid=self.wnmcid,
        )


class NeteaseClient:
    """持有网易云 Cookie、XEAPI 会话和动态公钥。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.cookie = load_credential()
        self._public_key: XeapiPublicKey | None = xeapi_public_key_from_values(load_public_key_state())
        self._session_id = ""
        self._session_key: bytes | None = None

    @property
    def logged_in(self) -> bool:
        return bool(self.cookie.get("MUSIC_U"))

    def _base_cookie(self) -> dict[str, str]:
        return DeviceContext.from_values(self.cookie).cookie_values()

    @staticmethod
    def _header_cookie(cookie: dict[str, str]) -> str:
        return "; ".join(f"{key}={value}" for key, value in cookie.items() if value)

    async def eapi(self, uri: str, data: dict[str, object]) -> tuple[dict[str, object], httpx.Response]:
        if not uri.startswith("/api/"):
            raise ValueError("EAPI URI 必须以 /api/ 开头")
        device = DeviceContext.from_values(self.cookie)
        cookie = device.cookie_values()
        header = {
            "os": device.os,
            "deviceId": device.device_id,
            "osver": device.osver,
            "appver": device.appver,
            "versioncode": device.versioncode,
            "mobilename": device.mobilename,
            "buildver": device.buildver,
            "resolution": device.resolution,
            "__csrf": device.csrf,
            "channel": device.channel,
            "requestId": f"{int(time.time() * 1000)}_{secrets.randbelow(1000):04d}",
        }
        body = dict(data)
        body["e_r"] = False
        body["header"] = header
        ua = UA_PC_CLIENT if device.os == "pc" else (UA_ANDROID if device.os == "android" else UA_WEB)
        headers = {
            "User-Agent": ua,
            "Referer": "https://music.163.com/",
            "Cookie": self._header_cookie(cookie),
        }
        try:
            response = await self.client.post(
                f"{EAPI_HOST}/eapi/{uri.removeprefix('/api/')}",
                data=eapi_form(uri, body),
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ParseException("网易云 EAPI 请求失败") from exc
        return _json_object(response.content), response

    async def weapi_raw(self, uri: str, data: dict[str, object]) -> tuple[dict[str, object], httpx.Response]:
        from .crypto import weapi_form

        if not uri.startswith("/api/"):
            raise ValueError("WEAPI URI 必须以 /api/ 开头")
        cookie = self._base_cookie()
        body = dict(data)
        body["csrf_token"] = cookie.get("__csrf", "")
        headers = {
            "User-Agent": UA_WEB,
            "Referer": "https://music.163.com/",
            "Cookie": self._header_cookie(cookie),
        }
        try:
            response = await self.client.post(
                f"{WEAPI_HOST}/weapi/{uri.removeprefix('/api/')}",
                data=weapi_form(body),
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ParseException("网易云 WEAPI 请求失败") from exc
        return _json_object(response.content), response

    async def weapi(self, uri: str, data: dict[str, object]) -> dict[str, object]:
        result, _ = await self.weapi_raw(uri, data)
        return result

    async def _ensure_public_key(self) -> XeapiPublicKey:
        if self._public_key is not None:
            return self._public_key
        nonce = "".join(str(random.randrange(10)) for _ in range(16))
        timestamp = str(int(time.time() * 1000))
        device_id = self.cookie.get("deviceId", "")
        data = {
            "appVersion": "9.5.61",
            "currentKeyVersion": "",
            "deviceId": device_id,
            "nonce": nonce,
            "os": "android",
            "requestType": "active",
            "signature": xeapi_sign(timestamp, nonce),
            "t1": "",
            "t2": "",
            "timestamp": timestamp,
            "uid": "",
        }
        try:
            response = await self.client.post(
                f"{API_HOST}/api/bsr/sk/get",
                data=data,
                headers={
                    "User-Agent": UA_ANDROID,
                    "Cookie": f"deviceId={device_id}" if device_id else "",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ParseException("网易云 XEAPI 公钥请求失败") from exc
        body = _json_object(response.content)
        if body.get("code") != 200:
            raise ParseException("网易云 XEAPI 公钥接口返回错误")
        raw = body.get("data")
        if not isinstance(raw, dict):
            raise ParseException("网易云 XEAPI 公钥响应无效")
        encrypted = raw.get("encryptedData")
        signature = raw.get("signature")
        response_timestamp = raw.get("timestamp")
        if (
            not isinstance(encrypted, str)
            or not isinstance(signature, str)
            or not isinstance(response_timestamp, (str, int))
        ):
            raise ParseException("网易云 XEAPI 公钥字段缺失")
        if not hmac.compare_digest(xeapi_sign(str(response_timestamp), nonce), signature):
            raise ParseException("网易云 XEAPI 公钥签名不匹配")
        self._public_key = xeapi_decrypt_public_key(encrypted)
        save_public_key_state(
            self._public_key.version,
            base64.b64encode(self._public_key.public_key).decode(),
            self._public_key.sk,
        )
        return self._public_key

    async def xeapi(self, uri: str, data: dict[str, object]) -> dict[str, object]:
        state = await self._ensure_public_key()
        cookie = self._base_cookie()
        cookie["os"] = "android"
        cookie["osver"] = "16"
        buildver = cookie["buildver"]
        cookie["appver"] = "9.5.61"
        cookie["sDeviceId"] = cookie.get("sDeviceId", cookie.get("deviceId", ""))
        cookie["buildver"] = buildver
        if self.cookie.get("MUSIC_U"):
            cookie["MUSIC_U"] = self.cookie["MUSIC_U"]
        if self.cookie.get("MUSIC_A"):
            cookie["MUSIC_A"] = self.cookie["MUSIC_A"]
        headers = {
            "User-Agent": UA_ANDROID,
            "Referer": "https://music.163.com/",
            "Cookie": self._header_cookie(cookie),
            "X-Client-Enc-State": "ENCRYPTED",
            "x-aeapi": "true",
            "content-type": "application/x-www-form-urlencoded;charset=utf-8",
            "x-deviceid": cookie.get("deviceId", ""),
            "x-os": "android",
            "x-osver": "16",
            "x-appver": "9.5.61",
            "x-sdeviceid": cookie.get("sDeviceId", ""),
            "x-buildver": buildver,
        }
        if self.cookie.get("MUSIC_U"):
            headers["x-music-u"] = self.cookie["MUSIC_U"]
        fields = xeapi_form(
            uri,
            data,
            state=state,
            os_name="android",
            session_id=self._session_id,
            session_key=self._session_key,
        )
        try:
            response = await self.client.post(
                f"{XEAPI_HOST}/xeapi/{uri.removeprefix('/api/')}",
                data=fields.as_form(),
                headers=headers,
            )
            response.raise_for_status()
            ssid = response.headers.get("x-encr-ssid")
            sskey = response.headers.get("x-encr-sskey")
            if ssid and sskey:
                self._session_id = ssid
                self._session_key = sskey.encode("utf-8")
            return xeapi_response_decrypt(response.content)
        except httpx.HTTPError as exc:
            raise ParseException("网易云 XEAPI 请求失败") from exc


def _json_object(body: bytes) -> dict[str, object]:
    try:
        value = msgspec.json.decode(body)
    except msgspec.DecodeError as exc:
        raise ParseException("网易云接口返回不是 JSON") from exc
    if not isinstance(value, dict):
        raise ParseException("网易云接口返回结构无效")
    return {key: item for key, item in value.items() if isinstance(key, str)}
