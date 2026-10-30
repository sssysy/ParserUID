"""汽水音乐接口数据模型。"""

from __future__ import annotations

from msgspec import Struct


class AvatarUrl(Struct, frozen=True):
    urls: list[str] = []


class ArtistInfo(Struct, frozen=True):
    medium_avatar_url: AvatarUrl | None = None
    url_avatar: AvatarUrl | None = None


class AlbumInfo(Struct, frozen=True):
    name: str = ""


class TrackInfo(Struct, frozen=True):
    artists: list[ArtistInfo] = []
    album: AlbumInfo = AlbumInfo()


class LyricWord(Struct, frozen=True):
    text: str = ""


class LyricSentence(Struct, frozen=True):
    startMs: int = 0
    text: str = ""
    words: list[LyricWord] = []


class LyricInfo(Struct, frozen=True):
    sentences: list[LyricSentence] = []


class QSTrackData(Struct, frozen=True):
    url: str = ""
    trackName: str = "未知歌曲"
    artistName: str = "未知歌手"
    duration: float = 0.0
    coverURL: str = ""
    trackInfo: TrackInfo = TrackInfo()
    lyrics: LyricInfo = LyricInfo()


class QSTrackPage(Struct, frozen=True):
    audioWithLyricsOption: QSTrackData = QSTrackData()


class QSLoaderData(Struct, frozen=True):
    track_page: QSTrackPage = QSTrackPage()


class QSRouterData(Struct, frozen=True):
    loaderData: QSLoaderData = QSLoaderData()
