"""酷狗音乐接口数据模型。"""

from __future__ import annotations

import re

from msgspec import Struct, field

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
    songName: str = "未知歌曲"
    singerName: str = "未知歌手"
    timeLength: float = 0.0
    hash: str = ""
    album_audio_id: int = 0
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
