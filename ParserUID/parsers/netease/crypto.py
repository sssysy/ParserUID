"""网易云 EAPI、WEAPI、XEAPI 请求加密原语。"""

from __future__ import annotations

import gzip
import hmac
import json
import base64
import hashlib
import secrets
from typing import Final
from dataclasses import dataclass
from urllib.parse import urlencode
from collections.abc import Mapping

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.PublicKey import ECC

EAPI_KEY: Final[bytes] = b"e82ckenh8dichen8"
EAPI_SEPARATOR: Final[str] = "36cd479b6b5"
WEAPI_PRESET_KEY: Final[bytes] = b"0CoJUm6Qyw8W8jud"
WEAPI_IV: Final[bytes] = b"0102030405060708"
WEAPI_BASE62: Final[str] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
WEAPI_RSA_HEX: Final[str] = (
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2"
    "e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee2559"
    "32575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7"
)
WEAPI_RSA_EXPONENT: Final[int] = 65537
XEAPI_STATIC_KEY: Final[bytes] = bytes.fromhex("ab1d5a430f6bb04a3f01e81ddd72bd916d5ce591248ac128714806d7f8fb1b84")
XEAPI_SIGN_KEY: Final[bytes] = (
    b"mUHCwVNWJbunMqAHf5MImuirT6plvs6VSFW62MGHstFQxhBGdEoIhLItH3djc4+FB/OKty3+lL2rGeoFBpVe5g=="
)
XEAPI_SPKI_PREFIX: Final[bytes] = bytes.fromhex("302a300506032b656e032100")


@dataclass(frozen=True, slots=True)
class XeapiPublicKey:
    version: str
    public_key: bytes
    sk: str


@dataclass(frozen=True, slots=True)
class XeapiCipherFields:
    c: str
    s: str
    r: str

    def as_form(self) -> dict[str, str]:
        return {"C": self.c, "S": self.s, "R": self.r}


def _pad(data: bytes) -> bytes:
    size = 16 - len(data) % 16
    return data + bytes([size]) * size


def aes_ecb_encrypt(key: bytes, plaintext: bytes) -> bytes:
    return AES.new(key, AES.MODE_ECB).encrypt(_pad(plaintext))


def aes_ecb_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    plain = AES.new(key, AES.MODE_ECB).decrypt(ciphertext)
    if plain and 0 < plain[-1] <= 16:
        return plain[: -plain[-1]]
    return plain


def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    return AES.new(key, AES.MODE_CBC, iv).encrypt(_pad(plaintext))


def eapi_form(uri: str, payload: Mapping[str, object]) -> dict[str, str]:
    """构造 EAPI 表单；uri 必须以 /api/ 开头。"""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.md5(f"nobody{uri}use{text}md5forencrypt".encode("utf-8")).hexdigest()
    plain = f"{uri}-{EAPI_SEPARATOR}-{text}-{EAPI_SEPARATOR}-{digest}".encode("utf-8")
    return {"params": aes_ecb_encrypt(EAPI_KEY, plain).hex().upper()}


def eapi_response_decrypt(body: bytes) -> dict[str, object]:
    plain = aes_ecb_decrypt(EAPI_KEY, body)
    if plain[:2] == b"\x1f\x8b":
        plain = gzip.decompress(plain)
    return _decode_json_object(plain)


def weapi_form(payload: Mapping[str, object]) -> dict[str, str]:
    secret = "".join(secrets.choice(WEAPI_BASE62) for _ in range(16)).encode("ascii")
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    first = base64.b64encode(aes_cbc_encrypt(WEAPI_PRESET_KEY, WEAPI_IV, text))
    params = base64.b64encode(aes_cbc_encrypt(secret, WEAPI_IV, first)).decode("ascii")
    modulus = int(WEAPI_RSA_HEX, 16)
    encrypted = pow(int.from_bytes(secret[::-1], "big"), WEAPI_RSA_EXPONENT, modulus)
    return {"params": params, "encSecKey": encrypted.to_bytes(128, "big").hex()}


def xeapi_sign(timestamp: str, nonce: str) -> str:
    return base64.b64encode(hmac.new(XEAPI_SIGN_KEY, f"{timestamp}{nonce}".encode(), hashlib.sha256).digest()).decode()


def xeapi_decrypt_public_key(encrypted_data: str) -> XeapiPublicKey:
    plain = aes_ecb_decrypt(XEAPI_STATIC_KEY, base64.b64decode(encrypted_data))
    body = _decode_json_object(plain)
    version = body["version"]
    public_key = body["publicKey"]
    sk = body.get("sk", "")
    if not isinstance(version, (str, int)) or not isinstance(public_key, str) or not isinstance(sk, str):
        raise ValueError("XEAPI 公钥响应结构无效")
    return XeapiPublicKey(version=str(version), public_key=base64.b64decode(public_key), sk=sk)


def xeapi_public_key_from_values(values: Mapping[str, object]) -> XeapiPublicKey | None:
    version = values.get("version")
    public_key = values.get("publicKey")
    sk = values.get("sk", "")
    if not isinstance(version, str) or not isinstance(public_key, str) or not isinstance(sk, str):
        return None
    try:
        raw = base64.b64decode(public_key)
    except ValueError:
        return None
    if len(raw) != 32:
        return None
    return XeapiPublicKey(version=version, public_key=raw, sk=sk)


def _xeapi_plaintext(uri: str, payload: Mapping[str, object], content_type: str) -> bytes:
    fields: dict[str, str] = {}
    if content_type != "application/x-www-form-urlencoded;charset=utf-8":
        fields["contentType"] = content_type
    query = uri.partition("?")[2]
    if query:
        fields["queryString"] = f"{query}&e_r=true"
    else:
        fields["queryString"] = "e_r=true"
    body_data = dict(payload)
    body_data.pop("e_r", None)
    body = urlencode(body_data).encode("utf-8")
    if body:
        fields["content"] = body.decode("utf-8")
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _derive_xeapi_key(shared_secret: bytes, ephemeral_public: bytes) -> bytes:
    prk = hmac.new(bytes(32), shared_secret, hashlib.sha256).digest()
    return hmac.new(prk, ephemeral_public + b"\x01", hashlib.sha256).digest()[:16]


def _xeapi_wrap_s(dynamic_key: bytes, state: XeapiPublicKey, os_name: str) -> bytes:
    peer = ECC.construct(curve="X25519", point_x=int.from_bytes(state.public_key, "little"))
    ephemeral = ECC.generate(curve="X25519")
    ephemeral_public = ephemeral.public_key().export_key(format="raw")
    shared = peer.pointQ.__mul__(ephemeral.d).x.to_bytes(32, "little")
    aes_key = _derive_xeapi_key(shared, ephemeral_public)
    iv = get_random_bytes(12)
    cipher = AES.new(aes_key, AES.MODE_GCM, nonce=iv, mac_len=16)
    plain = f"{base64.b64encode(dynamic_key).decode()}|{os_name}|{state.sk}".encode()
    encrypted, tag = cipher.encrypt_and_digest(plain)
    return ephemeral_public + iv + encrypted + tag


def xeapi_form(
    uri: str,
    payload: Mapping[str, object],
    *,
    state: XeapiPublicKey,
    os_name: str,
    session_id: str = "",
    session_key: bytes | None = None,
) -> XeapiCipherFields:
    dynamic_key = session_key or get_random_bytes(16)
    inner = aes_ecb_encrypt(
        XEAPI_STATIC_KEY, _xeapi_plaintext(uri, payload, "application/x-www-form-urlencoded;charset=utf-8")
    )
    random = get_random_bytes(16)
    xored = bytes(value ^ random[index & 15] for index, value in enumerate(inner))
    rot = (random[0] & 15) % len(xored)
    middle = random + xored[rot:] + xored[:rot]
    c = base64.urlsafe_b64encode(aes_ecb_encrypt(dynamic_key, middle)).rstrip(b"=").decode()
    s = base64.b64encode(_xeapi_wrap_s(dynamic_key, state, os_name)).decode()
    r_plain = f"{state.version}|{session_id if session_key else ''}".encode()
    r = base64.b64encode(aes_ecb_encrypt(XEAPI_STATIC_KEY, r_plain)).decode()
    return XeapiCipherFields(c=c, s=s, r=r)


def xeapi_response_decrypt(body: bytes) -> dict[str, object]:
    plain = aes_ecb_decrypt(EAPI_KEY, body)
    if plain[:2] == b"\x1f\x8b":
        plain = gzip.decompress(plain)
    return _decode_json_object(plain)


def _decode_json_object(data: bytes) -> dict[str, object]:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("响应不是 JSON 对象")
    return {key: item for key, item in value.items() if isinstance(key, str)}
