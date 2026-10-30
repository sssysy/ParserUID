"""酷我音乐接口数据模型。"""

from __future__ import annotations

from msgspec import Struct


class KuwoQuality(Struct, frozen=True):
    name: str = ""


class KuwoSongData(Struct, frozen=True):
    download_url: str = ""
    title: str = "未知歌曲"
    artist: str = "未知歌手"
    duration_seconds: float = 0.0
    album: str = ""
    cover: str = ""
    artist_pic: str = ""
    lyric: str = ""
    quality: KuwoQuality | str = ""


class KuwoShareResponse(Struct, frozen=True):
    code: int = 0
    data: KuwoSongData = KuwoSongData()


class BodianCategory(Struct, frozen=True):
    name: str = ""


class BodianInfoData(Struct, frozen=True):
    name: str = "未知歌曲"
    artist: str = "未知歌手"
    album: str = ""
    albumPic120: str = ""
    duration: float = 0.0
    categorys: list[BodianCategory] = []


class BodianInfoResponse(Struct, frozen=True):
    data: BodianInfoData = BodianInfoData()


class BodianMv(Struct, frozen=True):
    highUrl: str = ""
    lowUrl: str = ""
    coverUrl: str = ""
    duration: float = 0.0


class BodianMvData(Struct, frozen=True):
    mv: BodianMv = BodianMv()


class BodianMvResponse(Struct, frozen=True):
    data: BodianMvData = BodianMvData()


class BodianAudioData(Struct, frozen=True):
    audioUrl: str = ""


class BodianAudioResponse(Struct, frozen=True):
    data: BodianAudioData = BodianAudioData()
