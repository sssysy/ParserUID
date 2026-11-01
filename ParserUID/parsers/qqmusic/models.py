"""QQ音乐官方 CGI 接口与本地凭据数据模型。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from msgspec import Raw, Struct, field


class CgiSubResponse(Struct, frozen=True):
    """单个 CGI 子响应；data 保留原始 JSON 字节供具体模型解码。"""

    code: int = 0
    data: Raw = Raw(b"{}")


class CgiEnvelope(Struct, frozen=True):
    """QQ音乐 CGI 单请求信封。"""

    code: int = 0
    req_0: CgiSubResponse = field(default_factory=CgiSubResponse)


class QqSinger(Struct, frozen=True, omit_defaults=True):
    id: int = 0
    mid: str = ""
    name: str = ""
    title: str = ""
    pmid: str = ""


class QqAlbum(Struct, frozen=True, omit_defaults=True):
    id: int = 0
    mid: str = ""
    name: str = ""
    title: str = ""
    subtitle: str = ""
    time_public: str = ""
    pmid: str = ""


class QqFile(Struct, frozen=True, omit_defaults=True):
    media_mid: str = ""
    size_24aac: int = 0
    size_48aac: int = 0
    size_96aac: int = 0
    size_192aac: int = 0
    size_192ogg: int = 0
    size_128mp3: int = 0
    size_320mp3: int = 0
    size_ape: int = 0
    size_flac: int = 0
    size_dts: int = 0
    size_try: int = 0
    try_begin: int = 0
    try_end: int = 0
    size_96ogg: int = 0
    size_dolby: int = 0
    size_hires: int = 0
    size_new: list[int] = field(default_factory=list)


class QqPay(Struct, frozen=True, omit_defaults=True):
    pay_month: int = 0
    price_track: int = 0
    price_album: int = 0
    pay_play: int = 0
    pay_down: int = 0
    pay_status: int = 0
    time_free: int = 0


class QqTrack(Struct, frozen=True, omit_defaults=True):
    id: int = 0
    type: int = 0
    mid: str = ""
    name: str = ""
    title: str = ""
    subtitle: str = ""
    singer: list[QqSinger] = field(default_factory=list)
    album: QqAlbum = field(default_factory=QqAlbum)
    interval: int = 0
    isonly: int = 0
    language: int = 0
    genre: int = 0
    index_cd: int = 0
    index_album: int = 0
    time_public: str = ""
    status: int = 0
    label: str = ""
    bpm: int = 0
    ov: int = 0
    sa: int = 0
    es: str = ""
    vs: list[str] = field(default_factory=list)
    vi: list[int] = field(default_factory=list)
    vf: list[float] = field(default_factory=list)
    file: QqFile = field(default_factory=QqFile)
    pay: QqPay = field(default_factory=QqPay)


class QqInfoContent(Struct, frozen=True, omit_defaults=True):
    id: int = 0
    value: str = ""
    mid: str = ""
    jumpurl: str = ""


class QqInfoSection(Struct, frozen=True, omit_defaults=True):
    title: str = ""
    content: list[QqInfoContent] = field(default_factory=list)


class QqDetailExtras(Struct, frozen=True, omit_defaults=True):
    name: str = ""
    transname: str = ""
    subtitle: str = ""
    wikiurl: str = ""


class QqDetailInfo(Struct, frozen=True, omit_defaults=True):
    company: QqInfoSection = field(default_factory=QqInfoSection)
    genre: QqInfoSection = field(default_factory=QqInfoSection)
    intro: QqInfoSection = field(default_factory=QqInfoSection)
    lan: QqInfoSection = field(default_factory=QqInfoSection)
    pub_time: QqInfoSection = field(default_factory=QqInfoSection)


class QqSongDetailData(Struct, frozen=True, omit_defaults=True):
    info: QqDetailInfo = field(default_factory=QqDetailInfo)
    extras: QqDetailExtras = field(default_factory=QqDetailExtras)
    track_info: QqTrack = field(default_factory=QqTrack)


class QqMidUrlInfo(Struct, frozen=True, omit_defaults=True):
    songmid: str = ""
    filename: str = ""
    purl: str = ""
    vkey: str = ""
    ekey: str = ""
    result: int = 0
    mid: str = ""


class QqSongUrlsData(Struct, frozen=True, omit_defaults=True):
    expiration: int = 0
    sip: list[str] = field(default_factory=list)
    midurlinfo: list[QqMidUrlInfo] = field(default_factory=list)


class QqCdnDispatchData(Struct, frozen=True, omit_defaults=True):
    sip: list[str] = field(default_factory=list)
    freeflowsip: list[str] = field(default_factory=list)
    expiration: int = 0


class QqLyricData(Struct, frozen=True, omit_defaults=True):
    songID: int = 0
    lyric: str = ""
    trans: str = ""
    crypt: int = 0


class QqLoginData(Struct, frozen=True, omit_defaults=True):
    openid: str = ""
    refresh_token: str = ""
    access_token: str = ""
    expired_at: int = 0
    musicid: int = 0
    musickey: str = ""
    unionid: str = ""
    str_musicid: str = ""
    refresh_key: str = ""
    musickeyCreateTime: int = 0
    keyExpiresIn: int = 0
    first_login: int = 0
    bindAccountType: int = 0
    needRefreshKeyIn: int = 0
    encryptUin: str = ""
    loginType: int = 0


class QqStoredCredentialData(Struct, frozen=True, omit_defaults=True):
    openid: str = ""
    refresh_token: str = ""
    access_token: str = ""
    expired_at: int = 0
    musicid: int = 0
    musickey: str = ""
    unionid: str = ""
    str_musicid: str = ""
    refresh_key: str = ""
    musickey_create_time: int = 0
    key_expires_in: int = 0
    login_type: int = 0
    bind_account_type: int = 0
    need_refresh_key_in: int = 0
    encrypt_uin: str = ""


class QqStoredCredential(Struct, frozen=True, omit_defaults=True):
    schema_version: int = 1
    saved_at: int = 0
    credential: QqStoredCredentialData = field(default_factory=QqStoredCredentialData)


@dataclass(frozen=True, slots=True)
class QqCredential:
    """解析器运行时使用的 QQ音乐登录凭证。"""

    musicid: int = 0
    musickey: str = ""
    login_type: int = 0
    openid: str = ""
    refresh_token: str = ""
    refresh_key: str = ""
    access_token: str = ""
    expired_at: int = 0
    unionid: str = ""
    str_musicid: str = ""
    musickey_create_time: int = 0
    key_expires_in: int = 0
    encrypt_uin: str = ""
    bind_account_type: int = 0
    need_refresh_key_in: int = 0

    @property
    def is_valid(self) -> bool:
        return self.musicid > 0 and bool(self.musickey)

    @property
    def display_id(self) -> str:
        return self.str_musicid or str(self.musicid)

    @property
    def expires_at(self) -> int:
        if self.musickey_create_time <= 0 or self.key_expires_in <= 0:
            return 0
        return self.musickey_create_time + self.key_expires_in

    @property
    def is_expired(self) -> bool:
        expires_at = self.expires_at
        return expires_at > 0 and time.time() >= expires_at

    def needs_refresh(self, margin_seconds: int = 43200) -> bool:
        expires_at = self.expires_at
        return expires_at > 0 and time.time() >= expires_at - max(0, margin_seconds)

    def to_stored(self) -> QqStoredCredentialData:
        return QqStoredCredentialData(
            openid=self.openid,
            refresh_token=self.refresh_token,
            access_token=self.access_token,
            expired_at=self.expired_at,
            musicid=self.musicid,
            musickey=self.musickey,
            unionid=self.unionid,
            str_musicid=self.str_musicid,
            refresh_key=self.refresh_key,
            musickey_create_time=self.musickey_create_time,
            key_expires_in=self.key_expires_in,
            login_type=self.login_type,
            bind_account_type=self.bind_account_type,
            need_refresh_key_in=self.need_refresh_key_in,
            encrypt_uin=self.encrypt_uin,
        )

    @classmethod
    def from_stored(cls, data: QqStoredCredentialData) -> QqCredential:
        login_type = data.login_type or (1 if data.musickey.startswith("W_X_") else 2)
        return cls(
            musicid=data.musicid,
            musickey=data.musickey,
            login_type=login_type,
            openid=data.openid,
            refresh_token=data.refresh_token,
            refresh_key=data.refresh_key,
            access_token=data.access_token,
            expired_at=data.expired_at,
            unionid=data.unionid,
            str_musicid=data.str_musicid or (str(data.musicid) if data.musicid > 0 else ""),
            musickey_create_time=data.musickey_create_time,
            key_expires_in=data.key_expires_in,
            encrypt_uin=data.encrypt_uin,
            bind_account_type=data.bind_account_type,
            need_refresh_key_in=data.need_refresh_key_in,
        )
