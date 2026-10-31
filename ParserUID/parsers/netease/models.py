"""网易云音乐官方接口数据模型。"""

from __future__ import annotations

from msgspec import Struct


class SongArtist(Struct, frozen=True):
    name: str = ""


class SongAlbum(Struct, frozen=True):
    name: str = ""
    picUrl: str = ""


class SongDetail(Struct, frozen=True):
    id: int = 0
    name: str = ""
    duration: int = 0
    artists: list[SongArtist] = []
    album: SongAlbum = SongAlbum()


class SongDetailResponse(Struct, frozen=True):
    code: int = 0
    songs: list[SongDetail] = []


class LyricBody(Struct, frozen=True):
    lyric: str = ""


class SongLyricResponse(Struct, frozen=True):
    lrc: LyricBody = LyricBody()


class SongUrlItem(Struct, frozen=True):
    url: str = ""
    br: int = 0
    type: str = ""
    level: str = ""


class SongUrlResponse(Struct, frozen=True):
    code: int = 0
    data: list[SongUrlItem] = []
