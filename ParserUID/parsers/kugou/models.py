"""酷狗音乐接口数据模型。"""

from __future__ import annotations

import re
from typing import Literal
from dataclasses import dataclass

from msgspec import Raw, Struct, field

_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9A-Fa-f]{4})")


def decode_escaped_text(value: str) -> str:
    """还原分享页中二次转义的 Unicode 文本。"""
    if "\\u" not in value:
        return value
    return _UNICODE_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), value)


class KugouShareAuthor(Struct):
    author_id: int = 0
    avatar: str = ""
    author_name: str = ""


class KugouShareData(Struct):
    hash: str = ""
    req_hash: str = ""
    url: str = ""
    backup_url: list[str] = field(default_factory=list)
    songName: str = ""
    singerName: str = ""
    timeLength: float = 0.0
    album_audio_id: int = 0
    album_img: str = ""
    bitRate: int = 0
    fileName: str = ""
    authors: list[KugouShareAuthor] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.songName = decode_escaped_text(self.songName)
        self.fileName = decode_escaped_text(self.fileName)


class KugouShareSongInfo(Struct):
    data: KugouShareData = field(default_factory=KugouShareData)


class KugouSharePageParam(Struct):
    song_info: KugouShareSongInfo = field(default_factory=KugouShareSongInfo)


class KugouPlayInfo(Struct):
    errcode: int = -1
    url: str = ""
    error: str = ""
    songName: str = "未知歌曲"
    singerName: str = "未知歌手"
    author_name: str = ""
    timeLength: float = 0.0
    hash: str = ""
    album_audio_id: int = 0
    albumid: int = 0
    req_albumid: int = 0
    album_img: str = ""
    bitRate: int = 0
    fileName: str = ""
    privilege: int = 0
    extra: Raw | None = None

    def __post_init__(self) -> None:
        self.songName = decode_escaped_text(self.songName)
        self.singerName = decode_escaped_text(self.singerName)
        self.author_name = decode_escaped_text(self.author_name)
        self.fileName = decode_escaped_text(self.fileName)


class KugouTransParam(Struct):
    ogg_128_hash: str = ""
    ogg_128_filesize: int = 0
    ogg_320_hash: str = ""
    ogg_320_filesize: int = 0
    union_cover: str = ""


class KugouRelateInfo(Struct):
    bitrate: int = 0
    extname: str = ""
    filesize: int = 0


class KugouRelateGood(Struct):
    level: int = 0
    quality: str = ""
    hash: str = ""
    privilege: int = -1
    status: int = 0
    pay_type: int = -1
    price: int = 0
    fail_process: int = 0
    _msg: str = field(default="", name="_msg")
    info: KugouRelateInfo = field(default_factory=KugouRelateInfo)


class KugouPrivilegeResource(Struct):
    hash: str = ""
    album_id: int = 0
    album_audio_id: int = 0
    name: str = ""
    singername: str = ""
    albumname: str = ""
    trans_param: KugouTransParam = field(default_factory=KugouTransParam)
    relate_goods: list[KugouRelateGood] = field(default_factory=list)


class KugouPrivilegeResponse(Struct):
    status: int = -1
    error_code: int = 0
    error: str = ""
    data: list[KugouPrivilegeResource] = field(default_factory=list)


class KugouUrlResponse(Struct):
    status: int = -1
    error_code: int = 0
    error: str = ""
    url: list[str] = field(default_factory=list)
    backup_url: list[str] = field(default_factory=list)
    fileSize: int = 0
    extName: str = ""
    bitRate: int = 0
    timeLength: float = 0.0


class KugouAuthorizationData(Struct):
    auth: str = ""
    open_time: int = 0


class KugouAuthorizationResponse(Struct):
    status: int = -1
    error_code: int = 0
    error: str = ""
    data: KugouAuthorizationData = field(default_factory=KugouAuthorizationData)


class KugouQrKeyData(Struct):
    qrcode: str = ""
    qrcode_img: str = ""


class KugouQrKeyResponse(Struct):
    status: int = -1
    error_code: int = 0
    error: str = ""
    data: KugouQrKeyData = field(default_factory=KugouQrKeyData)


class KugouQrStatusData(Struct):
    status: int = 0
    token: str = ""
    userid: int = 0
    nickname: str = ""
    username: str = ""
    vip_type: int = 0
    vip_token: str = ""


class KugouQrStatusResponse(Struct):
    status: int = -1
    error_code: int = 0
    error: str = ""
    data: KugouQrStatusData = field(default_factory=KugouQrStatusData)


class KugouVipProduct(Struct):
    is_vip: int = 0
    vip_end_time: str = ""
    product_type: str = ""
    busi_type: str = ""


class KugouVipData(Struct):
    busi_vip: list[KugouVipProduct] = field(default_factory=list)
    vip_type: int = 0


class KugouVipResponse(Struct):
    status: int = -1
    error_code: int = 0
    error: str = ""
    data: KugouVipData = field(default_factory=KugouVipData)


class KugouShareHashData(Struct):
    hash: str = ""
    mixsongid: int = 0
    album_id: int = 0


class KugouShareHashInfo(Struct):
    data: KugouShareHashData = field(default_factory=KugouShareHashData)


class KugouShareHashResponse(Struct):
    status: int = -1
    errcode: int = -1
    song_info: KugouShareHashInfo = field(default_factory=KugouShareHashInfo)


@dataclass(frozen=True, slots=True)
class SongIdentity:
    hash: str
    album_id: int = 0
    album_audio_id: int = 0
    audio_id: int = 0


@dataclass(frozen=True, slots=True)
class SongMeta:
    identity: SongIdentity
    title: str
    singer: str
    album: str = ""
    duration: float = 0.0
    cover_url: str = ""
    privilege: int = 0


Quality = Literal[
    "128",
    "320",
    "flac",
    "high",
    "super",
    "viper_clear",
    "viper_tape",
    "viper_atmos",
]


@dataclass(frozen=True, slots=True)
class QualityOption:
    quality: Quality
    level: int
    exists: bool
    bitrate: int
    extension: str
    hash: str
    size: int
    privilege: int
    status: int
    need_vip: bool
    obtainable: bool
    free_listen: bool
    require_vip_type: str
    message: str


@dataclass(frozen=True, slots=True)
class PrivilegeResult:
    identity: SongIdentity
    qualities: tuple[QualityOption, ...]
    name: str = ""
    singer: str = ""
    album: str = ""

    @property
    def best_free_quality(self) -> QualityOption | None:
        available = [item for item in self.qualities if item.obtainable and item.hash]
        return max(available, key=lambda item: (item.level, _quality_rank(item.quality)), default=None)


@dataclass(frozen=True, slots=True)
class ResolvedAudio:
    url: str
    backup_urls: tuple[str, ...]
    quality: QualityOption
    extension: str


@dataclass(frozen=True, slots=True)
class VipAttempt:
    ran: bool
    success: bool
    message: str = ""


def _quality_rank(quality: str) -> int:
    order = (
        "128",
        "320",
        "flac",
        "high",
        "super",
        "viper_clear",
        "viper_tape",
        "viper_atmos",
    )
    try:
        return order.index(quality)
    except ValueError:
        return -1


class LyricCandidate(Struct):
    id: str = ""
    accesskey: str = ""


class LyricSearchResponse(Struct):
    candidates: list[LyricCandidate] = []


class LyricDownloadResponse(Struct):
    error_code: int = 0
    content: str = ""
    charset: str = "utf-8"
