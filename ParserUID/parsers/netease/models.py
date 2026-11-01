"""网易云音乐接口响应模型。"""

from __future__ import annotations

from msgspec import Struct, field


class SongArtist(Struct, frozen=True, omit_defaults=True):
    id: int = 0
    name: str = ""


class SongAlbum(Struct, frozen=True, omit_defaults=True):
    id: int = 0
    name: str = ""
    picUrl: str = ""


class QualityFile(Struct, frozen=True, omit_defaults=True):
    br: int = 0
    size: int = 0
    sr: int = 0
    vd: float = 0.0
    it: str = ""


class SongDetail(Struct, frozen=True, omit_defaults=True):
    id: int = 0
    name: str = ""
    ar: list[SongArtist] = field(default_factory=list)
    al: SongAlbum = field(default_factory=SongAlbum)
    dt: int = 0
    fee: int = 0
    publishTime: int = 0
    mv: int = 0
    alia: list[str] = field(default_factory=list)
    h: QualityFile | None = None
    m: QualityFile | None = None
    low: QualityFile | None = field(default=None, name="l")
    sq: QualityFile | None = None
    hr: QualityFile | None = None


class FreeTrialPrivilege(Struct, frozen=True, omit_defaults=True):
    resConsumable: bool = False
    userConsumable: bool = False
    listenType: int | None = None
    cannotListenReason: int | None = None
    playReason: str | None = None
    freeLimitTagType: str | None = None


class ChargeInfo(Struct, frozen=True, omit_defaults=True):
    rate: int = 0
    chargeType: int = 0
    chargeMessage: str | None = None


class SongPrivilege(Struct, frozen=True, omit_defaults=True):
    id: int = 0
    fee: int = 0
    st: int = 0
    maxbr: int = 0
    playMaxbr: int = 0
    downloadMaxbr: int = 0
    maxBrLevel: str = ""
    playMaxBrLevel: str = ""
    downloadMaxBrLevel: str = ""
    plLevel: str = ""
    dlLevel: str = ""
    flLevel: str = ""
    toast: bool = False
    freeTrialPrivilege: FreeTrialPrivilege | None = None
    chargeInfoList: list[ChargeInfo] = field(default_factory=list)


class SongDetailResponse(Struct, frozen=True, omit_defaults=True):
    code: int = 0
    songs: list[SongDetail] = field(default_factory=list)
    privileges: list[SongPrivilege] = field(default_factory=list)


class QualityDetail(Struct, frozen=True, omit_defaults=True):
    songId: int = 0
    h: QualityFile | None = None
    m: QualityFile | None = None
    low: QualityFile | None = field(default=None, name="l")
    sq: QualityFile | None = None
    hr: QualityFile | None = None
    db: QualityFile | None = None
    jm: QualityFile | None = None
    je: QualityFile | None = None
    sk: QualityFile | None = None
    sks: list[QualityFile] = field(default_factory=list)
    vi: QualityFile | None = None


class QualityDetailResponse(Struct, frozen=True, omit_defaults=True):
    code: int = 0
    data: QualityDetail | None = None


class TrialInfo(Struct, frozen=True, omit_defaults=True):
    fragmentType: int = 0
    start: int = 0
    end: int = 0


class SongUrlItem(Struct, frozen=True, omit_defaults=True):
    id: int = 0
    url: str | None = None
    br: int = 0
    size: int = 0
    code: int = 0
    type: str | None = None
    level: str | None = None
    encodeType: str | None = None
    freeTrialInfo: TrialInfo | None = None


class SongUrlResponse(Struct, frozen=True, omit_defaults=True):
    code: int = 0
    data: list[SongUrlItem] | SongUrlItem | None = None


class LyricBody(Struct, frozen=True, omit_defaults=True):
    lyric: str = ""


class SongLyricResponse(Struct, frozen=True, omit_defaults=True):
    code: int = 0
    lrc: LyricBody = field(default_factory=LyricBody)
    tlyric: LyricBody = field(default_factory=LyricBody)


class QrKeyResponse(Struct, frozen=True, omit_defaults=True):
    code: int = 0
    unikey: str = ""


class QrCheckResponse(Struct, frozen=True, omit_defaults=True):
    code: int = 0
    message: str = ""


class AccountResponse(Struct, frozen=True, omit_defaults=True):
    code: int = 0


class XeapiKeyResponse(Struct, frozen=True, omit_defaults=True):
    code: int = 0


class XeapiKeyData(Struct, frozen=True, omit_defaults=True):
    encryptedData: str = ""
    signature: str = ""
    timestamp: str = ""
