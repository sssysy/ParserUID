"""QQ音乐官方 CGI 客户端。"""

from __future__ import annotations

import time
import secrets
from typing import Literal, TypeVar
from hashlib import sha256
from collections.abc import Mapping

import httpx
import msgspec

from .sign import hash33, zzc_sign
from .models import (
    CgiEnvelope,
    QqLoginData,
    QqLyricData,
    QqCredential,
    CgiSubResponse,
    QqSongUrlsData,
    QqSongDetailData,
    QqCdnDispatchData,
)
from ...utils.exceptions import ParseException

MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
MUSICS_URL = "https://u.y.qq.com/cgi-bin/musics.fcg"
REFERER = "https://y.qq.com/"
WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
ANDROID_UA = "QQMusic 20090008(android 15)"

PlatformKind = Literal["android", "desktop", "web"]
ModelT = TypeVar("ModelT", bound=msgspec.Struct)

_SUB_ERROR_MESSAGES: dict[int, str] = {
    1000: "登录凭证已失效，请重新登录",
    104400: "登录凭证已失效，请重新登录",
    104401: "登录凭证已失效，请重新登录",
    2000: "QQ音乐接口要求签名请求",
    2001: "QQ音乐请求过于频繁，请稍后再试",
    20261: "登录参数错误",
    20271: "验证码错误",
    20272: "账号绑定异常",
    20274: "账号绑定缺失",
    20277: "账号受限",
    20278: "账号受限",
    20279: "登录设备数量超限",
    20450: "账号已被封禁",
    104604: "操作过于频繁，请稍后再试",
}


class QqMusicClient:
    """使用官方 musicu/musics CGI 的轻量客户端。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def song_detail(self, value: int | str, credential: QqCredential | None) -> QqSongDetailData:
        param: dict[str, object]
        if isinstance(value, int) or (isinstance(value, str) and value.isdecimal()):
            param = {"song_id": int(value)}
        else:
            param = {"song_mid": value}
        sub = await self.call(
            "music.pf_song_detail_svr",
            "get_song_detail_yqq",
            param,
            platform="web",
            credential=credential,
        )
        return _decode_data(sub.data, QqSongDetailData, "歌曲详情")

    async def cdn_dispatch(self, credential: QqCredential | None) -> QqCdnDispatchData:
        guid = _new_guid()
        sub = await self.call(
            "music.audioCdnDispatch.cdnDispatch",
            "GetCdnDispatch",
            {
                "guid": guid,
                "uid": credential.display_id if credential is not None else "0",
                "use_new_domain": 1,
                "use_ipv6": 1,
            },
            platform="desktop",
            credential=credential,
        )
        return _decode_data(sub.data, QqCdnDispatchData, "CDN 调度")

    async def song_urls(
        self,
        *,
        song_mid: str,
        filenames: list[str],
        credential: QqCredential | None,
        encrypted: bool,
    ) -> QqSongUrlsData:
        guid = _new_guid()
        module = "music.vkey.GetEVkey" if encrypted else "music.vkey.GetVkey"
        method = "CgiGetEVkey" if encrypted else "UrlGetVkey"
        uin = credential.display_id if credential is not None and credential.is_valid else ""
        sub = await self.call(
            module,
            method,
            {
                "uin": uin,
                "filename": filenames,
                "guid": guid,
                "songmid": [song_mid] * len(filenames),
                "songtype": [0] * len(filenames),
                "ctx": 0,
            },
            platform="android",
            credential=credential,
        )
        return _decode_data(sub.data, QqSongUrlsData, "歌曲链接")

    async def lyric(self, track_id: int, song_mid: str, credential: QqCredential | None) -> QqLyricData:
        param: dict[str, object] = {
            "crypt": 0,
            "lrc_t": 0,
            "qrc": 0,
            "qrc_t": 0,
            "roma": 0,
            "roma_t": 0,
            "trans": 0,
            "trans_t": 0,
            "needSingingAnnotations": False,
            "type": 1,
        }
        if track_id > 0:
            param["songId"] = track_id
        elif song_mid:
            param["songMid"] = song_mid
        sub = await self.call(
            "music.musichallSong.PlayLyricInfo",
            "GetPlayLyricInfo",
            param,
            platform="web",
            credential=credential,
        )
        return _decode_data(sub.data, QqLyricData, "歌词")

    async def login_qq(self, code: str) -> QqCredential:
        sub = await self.call(
            "QQConnectLogin.LoginServer",
            "QQLogin",
            {"code": code},
            platform="desktop",
            credential=None,
            extra_comm={"tmeLoginType": "2"},
        )
        return _credential_from_login_data(_decode_data(sub.data, QqLoginData, "QQ 登录"))

    async def login_wx(self, code: str) -> QqCredential:
        sub = await self.call(
            "music.login.LoginServer",
            "Login",
            {"code": code, "strAppid": "wx48db31d50e334801"},
            platform="desktop",
            credential=None,
            extra_comm={"tmeLoginType": "1"},
        )
        return _credential_from_login_data(_decode_data(sub.data, QqLoginData, "微信登录"))

    async def call(
        self,
        module: str,
        method: str,
        param: dict[str, object],
        *,
        platform: PlatformKind,
        credential: QqCredential | None,
        extra_comm: Mapping[str, str] | None = None,
    ) -> CgiSubResponse:
        comm = self._build_comm(platform, credential)
        if extra_comm is not None:
            comm.update(extra_comm)
        payload: dict[str, object] = {
            "comm": comm,
            "req_0": {
                "module": module,
                "method": method,
                "param": param,
            },
        }
        sub = await self._execute(payload, platform, signed=False)
        if sub.code == 2000:
            sub = await self._execute(payload, platform, signed=True)
        if sub.code != 0:
            message = _SUB_ERROR_MESSAGES.get(sub.code, f"QQ音乐接口返回错误码 {sub.code}")
            raise ParseException(message)
        return sub

    async def _execute(
        self,
        payload: dict[str, object],
        platform: PlatformKind,
        *,
        signed: bool,
    ) -> CgiSubResponse:
        body = msgspec.json.encode(payload)
        url = MUSICS_URL if signed else MUSICU_URL
        params = {"_": str(int(time.time() * 1000)), "sign": zzc_sign(body)} if signed else None
        headers = {
            "User-Agent": _user_agent(platform),
            "Referer": REFERER,
            "Content-Type": "application/json",
        }
        try:
            response = await self._client.post(url, params=params, content=body, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ParseException("QQ音乐接口请求失败") from exc
        try:
            envelope = msgspec.json.decode(response.content, type=CgiEnvelope)
        except msgspec.DecodeError as exc:
            raise ParseException("QQ音乐接口返回格式异常") from exc
        if envelope.code != 0:
            raise ParseException(f"QQ音乐接口返回错误码 {envelope.code}")
        return envelope.req_0

    def _build_comm(self, platform: PlatformKind, credential: QqCredential | None) -> dict[str, str]:
        guid = _new_guid()
        uin = credential.musicid if credential is not None and credential.is_valid else 0
        g_tk = hash33(credential.musickey, 5381) if credential is not None and credential.musickey else 5381
        if platform == "web":
            return {
                "ct": "24",
                "cv": "4747474",
                "platform": "yqq.json",
                "chid": "0",
                "uin": str(uin),
                "g_tk": str(g_tk),
                "g_tk_new_20200303": str(g_tk),
                "format": "json",
                "inCharset": "utf-8",
                "outCharset": "utf-8",
                "notice": "0",
                "need_new_code": "1",
            }
        if platform == "desktop":
            return {
                "ct": "19",
                "cv": "2201",
                "chid": "0",
                "uin": str(uin),
                "g_tk": str(g_tk),
                "guid": guid,
            }
        comm = {
            "ct": "11",
            "cv": "20090008",
            "v": "20090008",
            "chid": "10003505",
            "tmeAppID": "qqmusic",
            "traceid": f"10002_{uin or guid}_{int(time.time())}",
            "OpenUDID": guid,
            "udid": guid,
            "aid": _device_id(credential),
            "os_ver": "15",
            "phonetype": "Pixel 8",
        }
        if credential is not None and credential.is_valid:
            comm["qq"] = str(credential.musicid)
            comm["authst"] = credential.musickey
            comm["tmeLoginType"] = str(credential.login_type or _infer_login_type(credential.musickey))
        return comm


def _decode_data(raw: msgspec.Raw, model: type[ModelT], stage: str) -> ModelT:
    try:
        return msgspec.json.decode(raw, type=model)
    except msgspec.DecodeError as exc:
        raise ParseException(f"QQ音乐{stage}数据格式异常") from exc


def _credential_from_login_data(data: QqLoginData) -> QqCredential:
    if data.musicid <= 0 or not data.musickey:
        raise ParseException("QQ音乐登录成功但凭证不完整")
    login_type = data.loginType or _infer_login_type(data.musickey)
    return QqCredential(
        musicid=data.musicid,
        musickey=data.musickey,
        login_type=login_type,
        openid=data.openid,
        refresh_token=data.refresh_token,
        refresh_key=data.refresh_key,
        access_token=data.access_token,
        unionid=data.unionid,
        str_musicid=data.str_musicid or str(data.musicid),
        musickey_create_time=data.musickeyCreateTime,
        key_expires_in=data.keyExpiresIn,
        encrypt_uin=data.encryptUin,
        bind_account_type=data.bindAccountType,
        need_refresh_key_in=data.needRefreshKeyIn,
    )


def _infer_login_type(musickey: str) -> int:
    return 1 if musickey.startswith("W_X_") else 2


def _new_guid() -> str:
    return secrets.token_hex(16).upper()


def _device_id(credential: QqCredential | None) -> str:
    seed = credential.musickey if credential is not None else "anonymous"
    return sha256(seed.encode("utf-8")).hexdigest()[:16]


def _user_agent(platform: PlatformKind) -> str:
    return ANDROID_UA if platform == "android" else WEB_UA
