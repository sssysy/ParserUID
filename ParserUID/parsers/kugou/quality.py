"""酷狗音质权益判断与链接解析。"""

from __future__ import annotations

import time
from collections.abc import Mapping

import httpx

from .crypto import sign_key
from .models import (
    Quality,
    VipAttempt,
    SongIdentity,
    QualityOption,
    ResolvedAudio,
    KugouRelateGood,
    KugouTransParam,
    PrivilegeResult,
)
from .request import KugouRequest, payload_int, payload_str, request_json
from ...utils.exceptions import ParseException

UPSTREAM_QUALITIES = (
    "128",
    "320",
    "flac",
    "high",
    "viper_atmos",
    "viper_tape",
    "viper_clear",
    "super",
    "multitrack",
)
QUALITY_ORDER: tuple[Quality, ...] = (
    "viper_tape",
    "viper_clear",
    "viper_atmos",
    "super",
    "high",
    "flac",
    "320",
    "128",
)
_LEVELS: dict[str, tuple[int, ...]] = {
    "128": (1, 2),
    "320": (4,),
    "flac": (5,),
    "high": (6,),
    "super": (7,),
}
_SUPER_VIP_QUALITIES = frozenset({"viper_tape", "viper_clear", "viper_atmos"})


def _raw_mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, dict) else None


def _relate_goods(values: object) -> list[KugouRelateGood]:
    if not isinstance(values, list):
        return []
    result: list[KugouRelateGood] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        info = value.get("info")
        relate = KugouRelateGood(
            level=_as_int(value.get("level")),
            quality=_as_str(value.get("quality")),
            hash=_as_str(value.get("hash")),
            privilege=_as_int(value.get("privilege"), -1),
            status=_as_int(value.get("status")),
            pay_type=_as_int(value.get("pay_type"), -1),
            price=_as_int(value.get("price")),
            fail_process=_as_int(value.get("fail_process")),
            _msg=_as_str(value.get("_msg")),
        )
        if isinstance(info, dict):
            relate.info.bitrate = _as_int(info.get("bitrate"))
            relate.info.extname = _as_str(info.get("extname"))
            relate.info.filesize = _as_int(info.get("filesize"))
        result.append(relate)
    return result


def _trans_param(value: object) -> KugouTransParam:
    if not isinstance(value, dict):
        return KugouTransParam()
    return KugouTransParam(
        ogg_128_hash=_as_str(value.get("ogg_128_hash")),
        ogg_128_filesize=_as_int(value.get("ogg_128_filesize")),
        ogg_320_hash=_as_str(value.get("ogg_320_hash")),
        ogg_320_filesize=_as_int(value.get("ogg_320_filesize")),
        union_cover=_as_str(value.get("union_cover")),
    )


def _pick_relate(goods: list[KugouRelateGood], quality: str) -> KugouRelateGood | None:
    for level in _LEVELS.get(quality, ()):
        for item in goods:
            if item.level == level and item.quality == quality:
                return item
    return next((item for item in goods if item.quality == quality), None)


def _quality_option(
    identity: SongIdentity,
    *,
    quality: Quality,
    good: KugouRelateGood | None,
    trans: KugouTransParam,
    is_lite: bool,
) -> QualityOption:
    if good is None:
        return QualityOption(
            quality=quality,
            level=0,
            exists=False,
            bitrate=0,
            extension="",
            hash="",
            size=0,
            privilege=-1,
            status=0,
            need_vip=False,
            obtainable=False,
            free_listen=False,
            require_vip_type="",
            message="",
        )
    need_vip = good.privilege != 0
    free_listen = is_lite and quality == "128" and need_vip
    obtainable = free_listen or (not need_vip and good.status != 0)
    ogg_hash = ""
    ogg_size = 0
    extension = good.info.extname
    if quality == "128" and trans.ogg_128_hash:
        ogg_hash = trans.ogg_128_hash
        ogg_size = trans.ogg_128_filesize
        extension = "ogg"
    elif quality == "320" and trans.ogg_320_hash:
        ogg_hash = trans.ogg_320_hash
        ogg_size = trans.ogg_320_filesize
        extension = "ogg"
    require_vip = ""
    if not obtainable and need_vip:
        require_vip = "suvip" if quality in _SUPER_VIP_QUALITIES else "concept"
    return QualityOption(
        quality=quality,
        level=good.level,
        exists=True,
        bitrate=good.info.bitrate,
        extension=extension,
        hash=ogg_hash or good.hash or identity.hash,
        size=ogg_size or good.info.filesize,
        privilege=good.privilege,
        status=good.status,
        need_vip=need_vip,
        obtainable=obtainable,
        free_listen=free_listen,
        require_vip_type=require_vip,
        message=good._msg,
    )


async def fetch_privilege(
    client: httpx.AsyncClient,
    request: KugouRequest,
    identity: SongIdentity,
) -> PrivilegeResult:
    payload = await request_json(
        client,
        request,
        path="/v2/get_res_privilege/lite",
        method="POST",
        router="media.store.kugou.com",
        body={
            "appid": request.profile.appid,
            "area_code": 1,
            "behavior": "play",
            "clientver": request.profile.clientver,
            "need_hash_offset": 1,
            "relate": 1,
            "support_verify": 1,
            "resource": [
                {
                    "type": "audio",
                    "page_id": 0,
                    "hash": identity.hash.lower(),
                    "album_id": identity.album_id,
                }
            ],
            "qualities": list(UPSTREAM_QUALITIES),
        },
        headers={"Content-Type": "application/json"},
    )
    if payload_int(payload, "status", -1) != 1:
        raise ParseException(payload_str(payload, "error") or "酷狗音质权益查询失败")
    resources = payload.get("data")
    resource = resources[0] if isinstance(resources, list) and resources else {}
    resource_map = _raw_mapping(resource) or {}
    try:
        decoded = msgspec_privilege(resource_map)
    except (ValueError, TypeError):
        decoded = None
    if decoded is None:
        raise ParseException("酷狗音质权益响应结构无效")
    qualities = tuple(
        _quality_option(
            identity,
            quality=quality,
            good=_pick_relate(decoded[0], quality),
            trans=decoded[1],
            is_lite=request.profile.is_lite,
        )
        for quality in QUALITY_ORDER
    )
    return PrivilegeResult(
        identity=identity,
        qualities=qualities,
        name=_as_str(resource_map.get("name")),
        singer=_as_str(resource_map.get("singername")),
        album=_as_str(resource_map.get("albumname")),
    )


def msgspec_privilege(
    resource: Mapping[str, object],
) -> tuple[list[KugouRelateGood], KugouTransParam] | None:
    goods = _relate_goods(resource.get("relate_goods"))
    if not goods and resource.get("hash") is None:
        return None
    return goods, _trans_param(resource.get("trans_param"))


async def _authorization(
    client: httpx.AsyncClient,
    request: KugouRequest,
    identity: SongIdentity,
) -> tuple[str, int]:
    payload = await request_json(
        client,
        request,
        path="/v1/authorization",
        router="trackercdngz.kugou.com",
        params={
            "authorization": "",
            "module_id": 51,
            "album_audio_id": identity.album_audio_id,
            "clientver": 11561,
            "hash": identity.hash.lower(),
        },
    )
    if payload_int(payload, "status", -1) != 1:
        raise ParseException(payload_str(payload, "error") or "酷狗歌曲授权失败")
    data = payload.get("data")
    data_map = _raw_mapping(data)
    if data_map is None:
        raise ParseException("酷狗歌曲授权响应结构无效")
    auth = _as_str(data_map.get("auth"))
    open_time = _as_int(data_map.get("open_time"))
    if not auth and not open_time:
        raise ParseException("酷狗未返回可用授权")
    return auth, open_time


async def fetch_vip_status(
    client: httpx.AsyncClient,
    request: KugouRequest,
) -> tuple[int, str]:
    """返回概念版会员类型和到期文本；失败时视为未确认会员。"""
    payload = await request_json(
        client,
        request,
        base_url="https://kugouvip.kugou.com",
        path="/v1/get_union_vip",
        params={
            "busi_type": "concept",
            "product_type": "svip",
            "opt_product_types": "dvip,qvip",
        },
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        return 0, ""
    vip_type = _as_int(data.get("vip_type"))
    products = data.get("busi_vip")
    if isinstance(products, list):
        for product in products:
            if not isinstance(product, dict) or _as_int(product.get("is_vip")) != 1:
                continue
            end = _as_str(product.get("vip_end_time"))
            if end:
                return max(vip_type, 1), end
    return vip_type, ""


async def resolve_audio(
    client: httpx.AsyncClient,
    request: KugouRequest,
    identity: SongIdentity,
    option: QualityOption,
) -> ResolvedAudio:
    if request.profile.is_lite:
        auth, open_time = await _authorization(client, request, identity)
        if open_time <= 0:
            open_time = int(time.time())
        payload = await request_json(
            client,
            request,
            path="/tracker/v5/url",
            router="trackcdngz.kugou.com",
            params={
                "album_id": identity.album_id,
                "area_code": 1,
                "module": "",
                "hash": option.hash.lower(),
                "need_m": 0,
                "ssa_flag": "is_fromtrack",
                "version": 11430,
                "open_time": open_time,
                "ptype": 0,
                "need_ogg": 1,
                "page_id": request.profile.page_id,
                "auth": auth,
                "mtype": 0,
                "quality": option.quality,
                "album_audio_id": identity.album_audio_id,
                "behavior": "play",
                "pid": request.profile.pid,
                "module_id": 51,
                "cmd": 26,
                "ppage_id": request.profile.ppage_id,
                "clientver": 11561,
                "pidversion": 3001,
                "cdnBackup": 1,
                "key": sign_key(
                    request.profile,
                    option.hash,
                    request.credential.mid,
                    request.credential.userid,
                ),
            },
        )
    else:
        payload = await request_json(
            client,
            request,
            path="/v5/url",
            router="trackercdn.kugou.com",
            params={
                "album_id": identity.album_id,
                "area_code": 1,
                "hash": option.hash.lower(),
                "ssa_flag": "is_fromtrack",
                "version": 11430,
                "page_id": request.profile.page_id,
                "quality": option.quality,
                "album_audio_id": identity.album_audio_id,
                "behavior": "play",
                "pid": request.profile.pid,
                "cmd": 26,
                "pidversion": 3001,
                "IsFreePart": 0,
                "ppage_id": request.profile.ppage_id,
                "cdnBackup": 1,
                "module": "",
                "clientver": 11430,
                "key": sign_key(
                    request.profile,
                    option.hash,
                    request.credential.mid,
                    request.credential.userid,
                ),
            },
        )
    if payload_int(payload, "status", -1) != 1:
        raise ParseException(payload_str(payload, "error") or "酷狗未返回音频地址")
    urls = payload.get("url")
    if not isinstance(urls, list):
        raise ParseException("酷狗音频地址响应无效")
    usable = [item for item in urls if isinstance(item, str) and item.startswith(("http://", "https://"))]
    if not usable:
        raise ParseException("酷狗未返回有效音频地址")
    backups = payload.get("backup_url")
    backup_urls = (
        tuple(
            item
            for item in backups
            if isinstance(item, str) and item.startswith(("http://", "https://")) and item not in usable
        )
        if isinstance(backups, list)
        else ()
    )
    extension = option.extension or _extension_from_url(usable[0]) or "mp3"
    return ResolvedAudio(
        url=usable[0],
        backup_urls=backup_urls,
        quality=option,
        extension=extension,
    )


async def try_claim_vip(
    client: httpx.AsyncClient,
    request: KugouRequest,
    *,
    current_vip_type: int,
    receive_day: str = "",
    upgrade: bool = False,
) -> VipAttempt:
    if current_vip_type:
        return VipAttempt(ran=False, success=True, message="已有会员")
    messages: list[str] = []
    success = False
    for receive_day in _candidate_receive_days(receive_day):
        try:
            payload = await request_json(
                client,
                request,
                path="/youth/v1/recharge/receive_vip_listen_song",
                method="POST",
                params={"source_id": 90139, "receive_day": receive_day},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except ParseException as exc:
            messages.append(str(exc))
            continue
        success = payload_int(payload, "status", 0) == 1
        if "error" in payload:
            messages.append(payload_str(payload, "error"))
        if success:
            break
    if upgrade:
        try:
            payload = await request_json(
                client,
                request,
                path="/youth/v1/listen_song/upgrade_vip_reward",
                method="POST",
                params={"kugouid": request.credential.userid, "ad_type": 1},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            success = success or payload_int(payload, "status", 0) == 1
        except ParseException as exc:
            messages.append(str(exc))
    return VipAttempt(ran=True, success=success, message="；".join(messages))


def _candidate_receive_days(receive_day: str) -> tuple[str, ...]:
    if receive_day:
        return (receive_day,)
    now = int(time.time())
    values = [time.strftime("%Y-%m-%d", time.localtime(now))]
    previous = time.strftime("%Y-%m-%d", time.localtime(now - 86400))
    if previous != values[0]:
        values.append(previous)
    return tuple(values)


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, int) and type(value) is not bool:
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return default


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _extension_from_url(url: str) -> str:
    from urllib.parse import urlparse

    suffix = urlparse(url).path.rsplit(".", 1)[-1].lower() if "." in urlparse(url).path else ""
    return suffix if suffix in {"mp3", "ogg", "flac", "m4a", "wav", "ape", "dsf", "dff"} else ""
