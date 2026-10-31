"""酷狗请求签名与 RSA/AES 加密原语。"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Final, Literal
from dataclasses import dataclass
from collections.abc import Mapping

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad

STANDARD_ANDROID_SALT: Final = "OIlwieks28dk2k092lksi2UIkp"
LITE_ANDROID_SALT: Final = "LnT6xpN3khm36zse0QzvmgTZ3waWdRSA"
WEB_SALT: Final = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
STANDARD_KEY_SALT: Final = "57ae12eb6890223e355ccfcb74edf70d"
LITE_KEY_SALT: Final = "185672dd44712f60bb1736df5a377e82"

STANDARD_RSA_PUBLIC_KEY: Final = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDIAG7QOELSYoIJvTFJhMpe1s/gbjDJX51HBNnEl5HXqTW6lQ7LC8jr9fWZTwusknp+sVGzwd40MwP6U5yDE27M/X1+UR4tvOGOqp94TJtQ1EPnWGWXngpeIW5GxoQGao1rmYWAu6oi1z9XkChrsUdC6DJE5E221wf/4WLFxwAtRQIDAQAB
-----END PUBLIC KEY-----"""
LITE_RSA_PUBLIC_KEY: Final = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDECi0Np2UR87scwrvTr72L6oO01rBbbBPriSDFPxr3Z5syug0O24QyQO8bg27+0+4kBzTBTBOZ/WWU0WryL1JSXRTXLgFVxtzIY41Pe7lPOgsfTCn5kZcvKhYKJesKnnJDNr5/abvTGf+rHG3YRwsCHcQ08/q6ifSioBszvb3QiwIDAQAB
-----END PUBLIC KEY-----"""

ApiMode = Literal["standard", "lite"]


@dataclass(frozen=True, slots=True)
class ApiProfile:
    """一套酷狗客户端身份及请求常量。"""

    mode: ApiMode
    appid: int
    clientver: int
    android_salt: str
    key_salt: str
    rsa_public_key: str
    page_id: int
    ppage_id: str
    pid: int

    @property
    def is_lite(self) -> bool:
        return self.mode == "lite"

    @property
    def display_name(self) -> str:
        return "概念版" if self.is_lite else "普通版"


STANDARD_PROFILE: Final = ApiProfile(
    mode="standard",
    appid=1005,
    clientver=20489,
    android_salt=STANDARD_ANDROID_SALT,
    key_salt=STANDARD_KEY_SALT,
    rsa_public_key=STANDARD_RSA_PUBLIC_KEY,
    page_id=151369488,
    ppage_id="463467626,350369493,788954147",
    pid=2,
)
LITE_PROFILE: Final = ApiProfile(
    mode="lite",
    appid=3116,
    clientver=11440,
    android_salt=LITE_ANDROID_SALT,
    key_salt=LITE_KEY_SALT,
    rsa_public_key=LITE_RSA_PUBLIC_KEY,
    page_id=967177915,
    ppage_id="356753938,823673182,967485191",
    pid=411,
)


def md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _stringify(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Mapping):
        return "{" + ",".join(f'"{key}":{_json_value(item)}' for key, item in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_json_value(item) for item in value) + "]"
    return str(value)


def _json_value(value: object) -> str:
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if value is None:
        return "null"
    return _stringify(value)


def signature_web(params: Mapping[str, object], body: str = "") -> str:
    payload = "".join(f"{key}={_stringify(value)}" for key, value in sorted(params.items()))
    return md5_hex(f"{WEB_SALT}{payload}{body}{WEB_SALT}")


def signature_android(profile: ApiProfile, params: Mapping[str, object], body: str = "") -> str:
    payload = "".join(f"{key}={_stringify(value)}" for key, value in sorted(params.items()))
    return md5_hex(f"{profile.android_salt}{payload}{body}{profile.android_salt}")


def sign_key(profile: ApiProfile, song_hash: str, mid: str, userid: str) -> str:
    return md5_hex(f"{song_hash.lower()}{profile.key_salt}{profile.appid}{mid}{userid or '0'}")


def sign_params_key(profile: ApiProfile, data: str) -> str:
    return md5_hex(f"{profile.appid}{profile.android_salt}{profile.clientver}{data}")


def random_upper(length: int) -> str:
    alphabet = "1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def mid_from_guid(guid: str) -> str:
    digest = md5_hex(guid)
    return str(int(digest, 16))


def aes_encrypt_cbc(
    plaintext: str,
    *,
    key: str,
    iv: str | None = None,
) -> str:
    raw_key = key.encode("utf-8")
    raw_iv = (iv or key[-16:]).encode("utf-8")
    cipher = AES.new(raw_key, AES.MODE_CBC, raw_iv)
    return cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size)).hex()


def aes_encrypt_random_key(plaintext: str) -> tuple[str, str]:
    temporary_key = random_upper(16).lower()
    key = md5_hex(temporary_key)[:32]
    return aes_encrypt_cbc(plaintext, key=key, iv=key[-16:]), temporary_key


def aes_decrypt_cbc(ciphertext: str, key: str, iv: str | None = None) -> str:
    raw_key = key.encode("utf-8")
    raw_iv = (iv or key[-16:]).encode("utf-8")
    cipher = AES.new(raw_key, AES.MODE_CBC, raw_iv)
    plain = unpad(cipher.decrypt(bytes.fromhex(ciphertext)), AES.block_size)
    return plain.decode("utf-8")


def rsa_encrypt_raw(plaintext: str, public_key_pem: str) -> str:
    key = RSA.import_key(public_key_pem)
    data = plaintext.encode("utf-8")
    if len(data) > key.size_in_bytes():
        raise ValueError("RSA 明文超过密钥长度")
    encrypted = pow(int.from_bytes(data, "big"), key.e, key.n)
    return encrypted.to_bytes(key.size_in_bytes(), "big").hex()


def rsa_encrypt_pkcs1(plaintext: str, public_key_pem: str) -> str:
    key = RSA.import_key(public_key_pem)
    cipher = PKCS1_v1_5.new(key)
    encrypted = cipher.encrypt(plaintext.encode("utf-8"))
    return encrypted.hex()


def base64_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def new_guid() -> str:
    raw = bytearray(secrets.token_bytes(16))
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    encoded = raw.hex()
    return f"{encoded[:8]}-{encoded[8:12]}-{encoded[12:16]}-{encoded[16:20]}-{encoded[20:]}"
