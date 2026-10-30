"""酷狗音乐接口数据模型。"""

from __future__ import annotations

from msgspec import Struct


class KugouPlayInfo(Struct):
    errcode: int = -1
    url: str = ""
    songName: str = "未知歌曲"
    singerName: str = "未知歌手"
    timeLength: float = 0.0
    hash: str = ""
    album_audio_id: str = ""
    album_img: str = ""
    bitRate: int = 0


class LyricCandidate(Struct):
    id: str = ""
    accesskey: str = ""


class LyricSearchResponse(Struct):
    candidates: list[LyricCandidate] = []


class LyricDownloadResponse(Struct):
    error_code: int = 0
    content: str = ""
    charset: str = "utf-8"
