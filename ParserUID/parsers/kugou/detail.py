"""酷狗歌曲标识归一化与元信息补全。"""

from __future__ import annotations

import re
import json
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
JSON_HASH_RE = re.compile(r'"hash"\s*:\s*"([0-9A-Fa-f]{32})"')
SMARTY_RE = re.compile(r"var dataFromSmarty\s*=\s*(\[.*?\]),", re.DOTALL)


def _to_int(val: int | str | None) -> int:
    if isinstance(val, int) and type(val) is not bool:
        return val
    if isinstance(val, str) and val.isdigit():
        return int(val)
    return 0


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
        album_id=_to_int(info.albumid) or _to_int(info.req_albumid),
        album_audio_id=_to_int(info.album_audio_id),
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
    duration = 0.0
    if isinstance(info.timeLength, (int, float)) and type(info.timeLength) is not bool:
        duration = float(info.timeLength)
    elif isinstance(info.timeLength, str) and info.timeLength.replace(".", "", 1).isdigit():
        duration = float(info.timeLength)
    return SongMeta(
        identity=identity,
        title=info.songName or info.fileName or "未知歌曲",
        singer=info.author_name or info.singerName or "未知歌手",
        duration=max(0.0, duration),
        cover_url=cover.replace("{size}", "480") if cover else "",
        privilege=info.privilege,
    )


async def resolve_share_hash(
    client: httpx.AsyncClient,
    url: str,
    *,
    chain: str = "",
) -> SongIdentity:
    target = f"https://m.kugou.com/share/song.html?chain={chain}" if chain else url
    try:
        response = await client.get(
            target,
            headers={"User-Agent": MOBILE_UA, "Referer": "https://m.kugou.com/"},
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ParseException("酷狗分享页请求失败") from exc

    from_query = identity_from_query(str(response.url))
    if from_query.hash and not re.fullmatch(r"4?a{31,32}", from_query.hash, re.IGNORECASE):
        return from_query

    text = response.text
    for match in JSON_HASH_RE.finditer(text):
        h = match.group(1).lower()
        if not re.fullmatch(r"4?a{31,32}", h, re.IGNORECASE):
            return SongIdentity(hash=h)

    smarty = SMARTY_RE.search(text)
    if smarty:
        try:
            parsed = json.loads(smarty.group(1))
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                first = parsed[0]
                val = first.get("hash")
                if isinstance(val, str) and re.fullmatch(r"[0-9A-Fa-f]{32}", val):
                    h = val.lower()
                    if not re.fullmatch(r"4?a{31,32}", h, re.IGNORECASE):
                        album_id = _to_int(first.get("album_id"))
                        album_audio_id = _to_int(first.get("mixsongid") or first.get("album_audio_id"))
                        return SongIdentity(hash=h, album_id=album_id, album_audio_id=album_audio_id)
        except (json.JSONDecodeError, TypeError):
            pass

    raise ParseException("未找到酷狗歌曲 hash")


def identity_from_query(url: str) -> SongIdentity:
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(url).query)
    song_hash = (query.get("hash") or [""])[0]
    album_id = _to_int((query.get("album_id") or ["0"])[0])
    album_audio_id = _to_int((query.get("album_audio_id") or ["0"])[0])
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
    if info.errcode != 0 and not info.songName and not info.fileName:
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
