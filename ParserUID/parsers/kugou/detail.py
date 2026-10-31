"""酷狗歌曲标识归一化与元信息补全。"""

from __future__ import annotations

import re
from collections.abc import Mapping

import httpx
import msgspec

from .models import (
    SongMeta,
    SongIdentity,
    KugouPlayInfo,
)
from .request import KugouRequest
from ...utils.exceptions import ParseException

SONG_INFO_URL = "https://m.kugou.com/app/i/getSongInfo.php"
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
HASH_RE = re.compile(r"[0-9A-Fa-f]{32}")


async def fetch_song_info(
    client: httpx.AsyncClient,
    song_hash: str,
    *,
    album_id: int = 0,
    album_audio_id: int = 0,
) -> KugouPlayInfo:
    params: dict[str, str | int] = {"cmd": "playInfo", "hash": song_hash.upper()}
    if album_id:
        params["album_id"] = album_id
    if album_audio_id:
        params["album_audio_id"] = album_audio_id
    try:
        response = await client.get(
            SONG_INFO_URL,
            params=params,
            headers={"User-Agent": MOBILE_UA, "Referer": "https://m.kugou.com/"},
        )
        response.raise_for_status()
        return msgspec.json.decode(response.content, type=KugouPlayInfo)
    except httpx.HTTPError as exc:
        raise ParseException("酷狗歌曲信息请求失败") from exc
    except msgspec.DecodeError as exc:
        raise ParseException("酷狗歌曲信息响应无效") from exc


def _extra_int(payload: Mapping[str, object] | None, key: str) -> int:
    if payload is None:
        return 0
    value = payload.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def identity_from_play_info(info: KugouPlayInfo) -> SongIdentity:
    return SongIdentity(
        hash=(info.hash or "").lower(),
        album_id=info.albumid or info.req_albumid,
        album_audio_id=info.album_audio_id,
        audio_id=_extra_int(_raw_extra(info.extra), "audio_id"),
    )


def _raw_extra(extra: msgspec.Raw | None) -> Mapping[str, object] | None:
    if extra is None:
        return None
    try:
        value = msgspec.json.decode(extra)
    except msgspec.DecodeError:
        return None
    return value if isinstance(value, Mapping) else None


def meta_from_play_info(info: KugouPlayInfo, *, hash_fallback: str = "") -> SongMeta:
    identity = identity_from_play_info(info)
    if not identity.hash:
        identity = SongIdentity(
            hash=hash_fallback.lower(),
            album_id=identity.album_id,
            album_audio_id=identity.album_audio_id,
            audio_id=identity.audio_id,
        )
    cover = info.album_img
    extra = _raw_extra(info.extra)
    if extra is not None:
        cover = _string(extra.get("union_cover")) or cover
    return SongMeta(
        identity=identity,
        title=info.songName or info.fileName or "未知歌曲",
        singer=info.author_name or info.singerName or "未知歌手",
        duration=max(0.0, float(info.timeLength)),
        cover_url=cover.replace("{size}", "480") if cover else "",
        privilege=info.privilege,
    )


async def resolve_share_hash(
    client: httpx.AsyncClient,
    url: str,
    *,
    chain: str = "",
) -> SongIdentity:
    if chain:
        target = f"https://m.kugou.com/share/song.html?chain={chain}"
    else:
        target = url
    try:
        response = await client.get(
            target,
            headers={"User-Agent": MOBILE_UA, "Referer": "https://m.kugou.com/"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ParseException("酷狗分享页请求失败") from exc
    text = response.text
    matches = HASH_RE.findall(text)
    if not matches:
        raise ParseException("未找到酷狗歌曲 hash")
    return SongIdentity(hash=matches[0].lower())


def identity_from_query(url: str) -> SongIdentity:
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(url).query)
    song_hash = (query.get("hash") or [""])[0]
    album_id = _int_value((query.get("album_id") or ["0"])[0])
    album_audio_id = _int_value((query.get("album_audio_id") or ["0"])[0])
    return SongIdentity(hash=song_hash.lower(), album_id=album_id, album_audio_id=album_audio_id)


async def resolve_identity(
    client: httpx.AsyncClient,
    request: KugouRequest,
    identity: SongIdentity,
) -> tuple[SongMeta, SongIdentity]:
    info = await fetch_song_info(
        client,
        identity.hash,
        album_id=identity.album_id,
        album_audio_id=identity.album_audio_id,
    )
    if info.errcode != 0 and not info.songName:
        raise ParseException("酷狗歌曲不存在或不可访问")
    meta = meta_from_play_info(info, hash_fallback=identity.hash)
    merged = SongIdentity(
        hash=meta.identity.hash or identity.hash,
        album_id=meta.identity.album_id or identity.album_id,
        album_audio_id=meta.identity.album_audio_id or identity.album_audio_id,
        audio_id=meta.identity.audio_id or identity.audio_id,
    )
    return meta, merged


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int_value(value: str) -> int:
    return int(value) if value.isdigit() else 0
