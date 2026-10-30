"""网易云音乐接口数据模型。"""

from __future__ import annotations

from msgspec import Struct


class SongInfo(Struct, frozen=True):
    name: str = "未知歌曲"
    singer: str = "未知歌手"
    duration: str = "00:00"
    picimg: str = ""


class SongInfoResponse(Struct, frozen=True):
    code: int = 0
    data: SongInfo = SongInfo()


class SongLyric(Struct, frozen=True):
    lrc: str = ""


class SongLyricResponse(Struct, frozen=True):
    code: int = 0
    data: SongLyric = SongLyric()


class SongUrl(Struct, frozen=True):
    url: str = ""


class SongUrlResponse(Struct, frozen=True):
    code: int = 0
    data: SongUrl = SongUrl()
