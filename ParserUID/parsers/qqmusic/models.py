"""QQ音乐接口数据模型。"""

from __future__ import annotations

from msgspec import Struct


class QualityInfo(Struct, frozen=True):
    name: str = ""


class QQMusicSongData(Struct, frozen=True):
    download_url: str = ""
    id: str = ""
    ekey: str = ""
    duration_seconds: float = 0.0
    title: str = "未知歌曲"
    artist: str = "未知歌手"
    album: str = ""
    cover: str = ""
    artist_pic: str = ""
    lyric: str = ""
    quality: QualityInfo | str = ""


class QQMusicResponse(Struct, frozen=True):
    code: int = 0
    data: QQMusicSongData = QQMusicSongData()
