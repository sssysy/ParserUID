"""酷狗设备注册与登录态刷新。"""

from __future__ import annotations

import json
import time
from dataclasses import replace

import httpx

from .auth import KugouCredential
from .crypto import (
    LITE_RSA_PUBLIC_KEY,
    STANDARD_RSA_PUBLIC_KEY,
    md5_hex,
    random_upper,
    aes_decrypt_cbc,
    aes_encrypt_cbc,
    rsa_encrypt_raw,
    rsa_encrypt_pkcs1,
    aes_encrypt_random_key,
)
from .request import KugouRequest, request_json
from ...utils.exceptions import ParseException

DEVICE_URL = "https://userservice.kugou.com"
LOGIN_TOKEN_URL = "https://login.user.kugou.com"
VIP_URL = "https://kugouvip.kugou.com"

LITE_T2_KEY = "fd14b35e3f81af3817a20ae7adae7020"
LITE_T2_IV = "17a20ae7adae7020"
LITE_T1_KEY = "5e4ef500e9597fe004bd09a46d8add98"
LITE_T1_IV = "04bd09a46d8add98"
STANDARD_LOGIN_KEY = "90b8382a1bb4ccdcf063102053fd75b8"
STANDARD_LOGIN_IV = "f063102053fd75b8"
LITE_LOGIN_KEY = "c24f74ca2820225badc01946dba4fdf7"
LITE_LOGIN_IV = "adc01946dba4fdf7"


def _device_payload(credential: KugouCredential) -> dict[str, object]:
    return {
        "availableRamSize": 4983533568,
        "availableRomSize": 48114719,
        "availableSDSize": 48114717,
        "basebandVer": "",
        "batteryLevel": 100,
        "batteryStatus": 3,
        "brand": "Redmi",
        "buildSerial": "unknown",
        "device": "marble",
        "imei": credential.guid,
        "imsi": "",
        "manufacturer": "Xiaomi",
        "uuid": credential.guid,
        "accelerometer": False,
        "accelerometerValue": "",
        "gravity": False,
        "gravityValue": "",
        "gyroscope": False,
        "gyroscopeValue": "",
        "light": False,
        "lightValue": "",
        "magnetic": False,
        "magneticValue": "",
        "orientation": False,
        "orientationValue": "",
        "pressure": False,
        "pressureValue": "",
        "step_counter": False,
        "step_counterValue": "",
        "temperature": False,
        "temperatureValue": "",
    }


async def register_device(client: httpx.AsyncClient, request: KugouRequest) -> KugouCredential:
    credential = request.credential
    key = random_upper(6).lower()
    derived = md5_hex(key)
    aes_key = derived[:16]
    aes_iv = derived[16:32]
    plaintext = json.dumps(_device_payload(credential), ensure_ascii=False, separators=(",", ":"))
    encrypted = aes_encrypt_cbc(plaintext, key=aes_key, iv=aes_iv)
    portrait = rsa_encrypt_pkcs1(
        json.dumps(
            {"aes": key, "uid": credential.userid, "token": credential.token},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        request.profile.rsa_public_key,
    )
    try:
        response = await client.post(
            f"{DEVICE_URL}/risk/v3/r_register_dev",
            params={
                "part": 1,
                "platid": 1,
                "p": portrait,
                **{
                    "dfid": credential.dfid or "-",
                    "mid": credential.mid,
                    "uuid": "-",
                    "appid": request.profile.appid,
                    "clientver": request.profile.clientver,
                    "clienttime": int(time.time()),
                },
            },
            content=encrypted.encode("utf-8"),
            headers={
                "User-Agent": "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi",
                "mid": credential.mid,
                "dfid": credential.dfid or "-",
                "clienttime": str(int(time.time())),
            },
        )
        response.raise_for_status()
        decrypted = aes_decrypt_cbc(response.content.hex(), aes_key, aes_iv)
        payload = json.loads(decrypted)
    except (httpx.HTTPError, ValueError) as exc:
        raise ParseException("酷狗设备注册失败") from exc
    if not isinstance(payload, dict) or payload.get("status") != 1:
        raise ParseException("酷狗设备注册未返回 dfid")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ParseException("酷狗设备注册响应结构无效")
    dfid = data.get("dfid")
    if not isinstance(dfid, str) or not dfid:
        raise ParseException("酷狗设备注册未返回 dfid")
    return replace(credential, dfid=dfid)


def _login_token_body(profile: str, credential: KugouCredential, timestamp_ms: int) -> dict[str, object]:
    is_lite = profile == "lite"
    encrypt_key = LITE_LOGIN_KEY if is_lite else STANDARD_LOGIN_KEY
    encrypt_iv = LITE_LOGIN_IV if is_lite else STANDARD_LOGIN_IV
    p3 = aes_encrypt_cbc(
        json.dumps(
            {"clienttime": timestamp_ms // 1000, "token": credential.token},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        key=encrypt_key,
        iv=encrypt_iv,
    )
    params, temp_key = aes_encrypt_random_key("{}")
    pk = rsa_encrypt_raw(
        json.dumps({"clienttime_ms": timestamp_ms, "key": temp_key}, ensure_ascii=False, separators=(",", ":")),
        LITE_RSA_PUBLIC_KEY if is_lite else STANDARD_RSA_PUBLIC_KEY,
    ).upper()
    t2 = aes_encrypt_cbc(
        f"{credential.guid}|0f607264fc6318a92b9e13c65db7cd3c|{credential.mac}|{credential.dev}|{timestamp_ms}",
        key=LITE_T2_KEY,
        iv=LITE_T2_IV,
    )
    t1 = aes_encrypt_cbc(
        f"{credential.t1}|{timestamp_ms}" if credential.t1 else f"|{timestamp_ms}",
        key=LITE_T1_KEY,
        iv=LITE_T1_IV,
    )
    body: dict[str, object] = {
        "dfid": credential.dfid or "-",
        "p3": p3,
        "plat": 1,
        "t1": t1 if is_lite else 0,
        "t2": t2 if is_lite else 0,
        "t3": "MCwwLDAsMCwwLDAsMCwwLDA=",
        "pk": pk,
        "params": params,
        "userid": credential.userid,
        "clienttime_ms": timestamp_ms,
    }
    if is_lite:
        body["dev"] = credential.dev
    return body


async def refresh_login(client: httpx.AsyncClient, request: KugouRequest) -> KugouCredential:
    credential = request.credential
    if not credential.has_login:
        raise ParseException("酷狗 Cookie 缺少 token 或 userid")
    timestamp_ms = int(time.time() * 1000)
    body = _login_token_body(request.profile.mode, credential, timestamp_ms)
    try:
        response = await client.post(
            f"{LOGIN_TOKEN_URL}/v5/login_by_token",
            json=body,
            headers={
                "User-Agent": "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi",
                "x-router": "login.user.kugou.com",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ParseException("酷狗登录态刷新失败") from exc
    if not isinstance(payload, dict) or payload.get("status") != 1:
        raise ParseException("酷狗登录态刷新未成功")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ParseException("酷狗登录态刷新响应结构无效")
    token = data.get("token")
    userid = data.get("userid")
    if not isinstance(token, str) or not token:
        raise ParseException("酷狗登录态刷新未返回 token")
    return replace(
        credential,
        token=token,
        userid=str(userid) if userid is not None else credential.userid,
        t1=str(data.get("t1") or credential.t1),
        vip_type=int(data.get("vip_type") or 0),
        vip_token=str(data.get("vip_token") or ""),
    )


async def query_vip_type(client: httpx.AsyncClient, request: KugouRequest) -> int:
    payload = await request_json(
        client,
        request,
        base_url=VIP_URL,
        path="/v1/get_union_vip",
        params={"busi_type": "concept"},
        method="GET",
    )
    data = payload.get("data")
    if isinstance(data, dict):
        vip_type = data.get("vip_type")
        if isinstance(vip_type, int):
            return vip_type
    return 0
