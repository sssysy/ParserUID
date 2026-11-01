"""QQ音乐音质档位、文件名编码与 VKey 取流。"""

from __future__ import annotations

from typing import Literal
from dataclasses import dataclass
from collections.abc import Sequence

from .client import QqMusicClient
from .models import QqFile, QqTrack, QqCredential, QqMidUrlInfo
from ...utils.exceptions import ParseException

QualityKey = Literal[
    "auto",
    "master",
    "atmos2",
    "atmos51",
    "atmos71",
    "dolby",
    "dts",
    "flac",
    "ogg640",
    "ogg320",
    "mp3_320",
    "ogg192",
    "aac192",
    "mp3_128",
    "ogg96",
    "aac96",
    "aac48",
]

QUALITY_OPTIONS: tuple[str, ...] = (
    "auto",
    "master",
    "atmos2",
    "atmos51",
    "atmos71",
    "dolby",
    "dts",
    "flac",
    "ogg640",
    "ogg320",
    "mp3_320",
    "ogg192",
    "aac192",
    "mp3_128",
    "ogg96",
    "aac96",
    "aac48",
)

_FALLBACK_CDNS: tuple[str, ...] = (
    "https://ws.stream.qqmusic.qq.com/",
    "https://isure.stream.qqmusic.qq.com/",
    "https://aqqmusic.tc.qq.com/",
)


@dataclass(frozen=True, slots=True)
class QualitySpec:
    key: QualityKey
    label: str
    start_code: str
    extension: str
    rank: int
    encrypted_start_code: str = ""
    encrypted_extension: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedAudio:
    url: str
    fallback_urls: tuple[str, ...]
    quality: QualitySpec
    extension: str
    ekey: str
    filename: str

    @property
    def quality_label(self) -> str:
        return self.quality.label


_SPECS: tuple[QualitySpec, ...] = (
    QualitySpec("master", "臻品母带", "AI00", ".flac", 16, "AIM0", ".mflac"),
    QualitySpec("atmos2", "臻品音质", "Q000", ".flac", 15, "Q0M0", ".mflac"),
    QualitySpec("atmos51", "臻品全景声 5.1", "Q001", ".flac", 14, "Q0M1", ".mflac"),
    QualitySpec("atmos71", "臻品全景声 7.1", "Q003", ".ogg", 13, "Q0M3", ".mgg"),
    QualitySpec("dolby", "杜比全景声", "D004", ".mp4", 12, "D0M4", ".mmp4"),
    QualitySpec("dts", "DTS:X", "DT03", ".mp4", 11, "DTM3", ".mmp4"),
    QualitySpec("flac", "SQ 无损 FLAC", "F000", ".flac", 10, "F0M0", ".mflac"),
    QualitySpec("ogg640", "SQ 无损 OGG", "O801", ".ogg", 9, "O8M1", ".mgg"),
    QualitySpec("ogg320", "HQ 高品质 OGG", "O800", ".ogg", 8, "O8M0", ".mgg"),
    QualitySpec("mp3_320", "HQ 高品质 MP3", "M800", ".mp3", 7),
    QualitySpec("ogg192", "HQ 高品质 OGG 192K", "O600", ".ogg", 6, "O6M0", ".mgg"),
    QualitySpec("aac192", "HQ 高品质 AAC 192K", "C600", ".m4a", 5),
    QualitySpec("mp3_128", "标准音质 MP3", "M500", ".mp3", 4),
    QualitySpec("ogg96", "流畅音质 OGG", "O400", ".ogg", 3, "O4M0", ".mgg"),
    QualitySpec("aac96", "流畅音质 AAC", "C400", ".m4a", 2),
    QualitySpec("aac48", "低品质 AAC", "C200", ".m4a", 1),
)


def normalize_quality(raw: str) -> QualityKey:
    value = raw.strip().lower()
    for option in QUALITY_OPTIONS:
        if option == value:
            return _quality_key(option)
    return "auto"


async def resolve_audio(
    client: QqMusicClient,
    track: QqTrack,
    credential: QqCredential | None,
    preferred: QualityKey,
) -> ResolvedAudio:
    dispatch = await client.cdn_dispatch(credential)
    cdns = _unique_strings((*dispatch.sip, *dispatch.freeflowsip, *_FALLBACK_CDNS))
    candidates = _candidates(track.file, preferred)
    if not candidates:
        raise ParseException("QQ音乐没有返回该歌曲的可用音质")

    plain_names = [_filename(track.mid, track.file.media_mid, spec, encrypted=False) for spec in candidates]
    plain_result = await client.song_urls(
        song_mid=track.mid,
        filenames=plain_names,
        credential=credential,
        encrypted=False,
    )
    resolved = _pick_resolved(
        track,
        candidates,
        plain_result.midurlinfo,
        cdns,
        encrypted=False,
    )
    if resolved is not None:
        return resolved

    encrypted_candidates = [spec for spec in candidates if spec.encrypted_start_code and spec.encrypted_extension]
    if encrypted_candidates:
        encrypted_names = [
            _filename(track.mid, track.file.media_mid, spec, encrypted=True) for spec in encrypted_candidates
        ]
        encrypted_result = await client.song_urls(
            song_mid=track.mid,
            filenames=encrypted_names,
            credential=credential,
            encrypted=True,
        )
        resolved = _pick_resolved(
            track,
            encrypted_candidates,
            encrypted_result.midurlinfo,
            cdns,
            encrypted=True,
        )
        if resolved is not None:
            return resolved

    raise ParseException(_permission_message(plain_result.midurlinfo, credential))


def _candidates(file: QqFile, preferred: QualityKey) -> list[QualitySpec]:
    ordered = list(_SPECS)
    if preferred != "auto":
        selected_rank = next((item.rank for item in ordered if item.key == preferred), ordered[-1].rank)
        ordered = [item for item in ordered if item.rank <= selected_rank]
    available = [item for item in ordered if _file_size(file, item.key) > 0]
    if preferred != "auto":
        requested = [item for item in ordered if item.key == preferred]
        if requested and requested[0] not in available:
            available.insert(0, requested[0])
    return available or [item for item in ordered if item.key == "mp3_128"]


def _pick_resolved(
    track: QqTrack,
    candidates: Sequence[QualitySpec],
    items: Sequence[QqMidUrlInfo],
    cdns: Sequence[str],
    *,
    encrypted: bool,
) -> ResolvedAudio | None:
    for spec in candidates:
        expected = _filename(track.mid, track.file.media_mid, spec, encrypted=encrypted)
        info = _find_info(items, expected)
        if info is None or info.result != 0 or not info.purl:
            continue
        urls = _build_urls(info.purl, cdns)
        if not urls:
            continue
        extension = spec.encrypted_extension if encrypted else spec.extension
        return ResolvedAudio(
            url=urls[0],
            fallback_urls=tuple(urls[1:]),
            quality=spec,
            extension=extension,
            ekey=info.ekey,
            filename=expected,
        )
    return None


def _filename(song_mid: str, media_mid: str, spec: QualitySpec, *, encrypted: bool) -> str:
    start_code = spec.encrypted_start_code if encrypted else spec.start_code
    extension = spec.encrypted_extension if encrypted else spec.extension
    if media_mid:
        return f"{start_code}{media_mid}{extension}"
    return f"{start_code}{song_mid}{song_mid}{extension}"


def _find_info(items: Sequence[QqMidUrlInfo], filename: str) -> QqMidUrlInfo | None:
    for item in items:
        if item.filename == filename:
            return item
    return None


def _build_urls(purl: str, cdns: Sequence[str]) -> list[str]:
    if purl.startswith(("http://", "https://")):
        return [purl]
    results: list[str] = []
    for cdn in cdns:
        if cdn:
            results.append(f"{cdn.rstrip('/')}/{purl.lstrip('/')}")
    return _unique_strings(results)


def _unique_strings(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _file_size(file: QqFile, quality: QualityKey) -> int:
    match quality:
        case "master":
            return _size_new(file, 0)
        case "atmos2":
            return _size_new(file, 1)
        case "atmos51":
            return _size_new(file, 2)
        case "atmos71":
            return _size_new(file, 6)
        case "dolby":
            return file.size_dolby
        case "dts":
            return _size_new(file, 9) or file.size_dts
        case "flac":
            return file.size_flac
        case "ogg640":
            return _size_new(file, 5)
        case "ogg320":
            return _size_new(file, 3)
        case "mp3_320":
            return file.size_320mp3
        case "ogg192":
            return file.size_192ogg
        case "aac192":
            return file.size_192aac
        case "mp3_128":
            return file.size_128mp3
        case "ogg96":
            return file.size_96ogg
        case "aac96":
            return file.size_96aac
        case "aac48":
            return file.size_48aac
        case "auto":
            return 0


def _size_new(file: QqFile, index: int) -> int:
    if index < len(file.size_new):
        return file.size_new[index]
    return 0


def _permission_message(items: Sequence[QqMidUrlInfo], credential: QqCredential | None) -> str:
    result_codes = [item.result for item in items if item.result != 0]
    if credential is None or not credential.is_valid:
        return "请先使用 QQ音乐登录后再解析高音质音频"
    if 104003 in result_codes:
        return "当前账号没有该歌曲的播放权限，可能需要会员"
    if 104013 in result_codes:
        return "该歌曲在当前播放设备受限"
    if 104004 in result_codes:
        return "QQ音乐 VKey 获取失败，请稍后重试"
    if 22 in result_codes:
        return "当前账号无权播放该音质，请确认登录状态或会员权限"
    return "QQ音乐没有返回可用音源"


def _quality_key(value: str) -> QualityKey:
    match value:
        case "auto":
            return "auto"
        case "master":
            return "master"
        case "atmos2":
            return "atmos2"
        case "atmos51":
            return "atmos51"
        case "atmos71":
            return "atmos71"
        case "dolby":
            return "dolby"
        case "dts":
            return "dts"
        case "flac":
            return "flac"
        case "ogg640":
            return "ogg640"
        case "ogg320":
            return "ogg320"
        case "mp3_320":
            return "mp3_320"
        case "ogg192":
            return "ogg192"
        case "aac192":
            return "aac192"
        case "mp3_128":
            return "mp3_128"
        case "ogg96":
            return "ogg96"
        case "aac96":
            return "aac96"
        case "aac48":
            return "aac48"
        case _:
            return "auto"
