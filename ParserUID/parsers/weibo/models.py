"""微博平台状态与元数据模型定义。"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass


def _dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return {}


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _int(value: object) -> int:
    try:
        return int(str(value or 0))
    except (TypeError, ValueError):
        return 0


def _first_url(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            url = value.strip()
            return f"https:{url}" if url.startswith("//") else url
        if isinstance(value, dict):
            url = _first_url(value.get("url"), value.get("src"))
            if url:
                return url
    return ""


def _largest_pic_url(pic: dict[str, object]) -> str:
    large = _dict(pic.get("large"))
    largest = _dict(pic.get("largest"))
    original = _dict(pic.get("original"))
    mw2000 = _dict(pic.get("mw2000"))
    return _first_url(
        largest.get("url"),
        original.get("url"),
        large.get("url"),
        mw2000.get("url"),
        pic.get("url"),
    )


@dataclass(frozen=True, slots=True)
class WeiboAuthor:
    uid: str
    name: str
    avatar_url: str
    description: str
    location: str


@dataclass(frozen=True, slots=True)
class WeiboPic:
    image_url: str
    live_video_url: str
    is_live: bool


@dataclass(frozen=True, slots=True)
class WeiboVideo:
    url: str
    fallback_urls: tuple[str, ...]
    cover_url: str
    duration: float
    title: str


@dataclass(frozen=True, slots=True)
class WeiboStatus:
    mid: str
    bid: str
    author: WeiboAuthor
    html_text: str
    raw_text: str
    timestamp: int
    pics: tuple[WeiboPic, ...]
    video: WeiboVideo | None
    retweeted: WeiboStatus | None
    region: str
    reposts_count: int
    comments_count: int
    attitudes_count: int
    is_long_text: bool

    @property
    def url(self) -> str:
        if self.author.uid and self.bid:
            return f"https://weibo.com/{self.author.uid}/{self.bid}"
        return f"https://m.weibo.cn/detail/{self.mid}"


def parse_weibo_timestamp(value: object) -> int:
    if isinstance(value, (int, float)) and value > 0:
        number = int(value)
        return number // 1000 if number >= 10**12 else number
    text = _string(value)
    if not text:
        return 0
    try:
        return int(datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y").timestamp())
    except ValueError:
        return 0


def _author(payload: object) -> WeiboAuthor:
    user = _dict(payload)
    uid = _string(user.get("idstr") or user.get("id"))
    return WeiboAuthor(
        uid=uid,
        name=_string(user.get("screen_name")) or "微博用户",
        avatar_url=_first_url(
            user.get("avatar_hd"),
            user.get("profile_image_url"),
            user.get("avatar_large"),
        ),
        description=_string(user.get("description")),
        location=_string(user.get("location")),
    )


def _pic(payload: dict[str, object], live_video: dict[str, object]) -> WeiboPic | None:
    image_url = _largest_pic_url(payload)
    if not image_url:
        return None
    raw_live = _string(payload.get("video") or payload.get("videoSrc"))
    if not raw_live:
        usid = _string(live_video.get("usid_hd") or live_video.get("usid"))
        if usid:
            raw_live = f"https://livephoto.us.sinaimg.cn/{usid}.mov"
    live_url = _first_url(raw_live)
    return WeiboPic(
        image_url=image_url,
        live_video_url=live_url,
        is_live=bool(
            live_url
            and (_string(payload.get("type")).lower() == "livephoto" or bool(payload.get("video")) or bool(live_video))
        ),
    )


def _pics(payload: dict[str, object]) -> tuple[WeiboPic, ...]:
    result: list[WeiboPic] = []
    live_payload = _dict(payload.get("live_photo_video_data"))
    live_links = [_string(item) for item in _list(payload.get("live_photo")) if item]
    infos = payload.get("pic_infos")
    if isinstance(infos, dict) and infos:
        for pic_id, item in infos.items():
            pic_payload = _dict(item)
            pic_payload.setdefault("pic_id", str(pic_id))
            pic = _pic(pic_payload, _dict(live_payload.get(str(pic_id))))
            if pic is not None:
                result.append(pic)
        return tuple(result)

    for index, item in enumerate(_list(payload.get("pics"))):
        if not isinstance(item, dict):
            continue
        pic_payload = _dict(item)
        pic = _pic(pic_payload, {})
        if pic is None:
            continue
        if pic.is_live and not pic.live_video_url and index < len(live_links):
            pic = WeiboPic(
                image_url=pic.image_url,
                live_video_url=_first_url(live_links[index]),
                is_live=True,
            )
        result.append(pic)
    return tuple(result)


def _video(payload: dict[str, object], fallback_title: str) -> WeiboVideo | None:
    page_info = _dict(payload.get("page_info"))
    media = _dict(page_info.get("media_info"))
    urls = _dict(page_info.get("urls"))

    candidates: list[str] = []
    for value in (
        urls.get("mp4_720p_mp4"),
        urls.get("mp4_hd_mp4"),
        urls.get("mp4_ld_mp4"),
        media.get("stream_url_hd"),
        media.get("stream_url"),
    ):
        url = _first_url(value)
        if url and url not in candidates:
            candidates.append(url)
    if not candidates:
        return None

    big_pic_info = _dict(media.get("big_pic_info"))
    cover = _first_url(page_info.get("page_pic"), big_pic_info.get("pic_big"))
    duration_value = media.get("duration")
    try:
        duration = float(str(duration_value or 0))
    except (TypeError, ValueError):
        duration = 0.0
    return WeiboVideo(
        url=candidates[0],
        fallback_urls=tuple(candidates[1:]),
        cover_url=cover,
        duration=duration,
        title=_string(page_info.get("page_title")) or fallback_title,
    )


def decode_status(payload: object) -> WeiboStatus:
    data = _dict(payload)
    if isinstance(data.get("data"), dict):
        data = _dict(data["data"])
    if not (data.get("id") or data.get("idstr") or data.get("mid")):
        raise ValueError("微博响应缺少状态数据")

    retweeted_payload = data.get("retweeted_status")
    retweeted = None
    if isinstance(retweeted_payload, dict):
        try:
            retweeted = decode_status(retweeted_payload)
        except ValueError:
            retweeted = None

    mid = _string(data.get("idstr") or data.get("id") or data.get("mid"))
    region = _string(data.get("region_name"))
    if region.startswith("发布于"):
        region = region.removeprefix("发布于").strip()
    author = _author(data.get("user"))
    return WeiboStatus(
        mid=mid,
        bid=_string(data.get("bid") or data.get("mblogid")),
        author=author,
        html_text=_string(data.get("text")),
        raw_text=_string(data.get("text_raw")),
        timestamp=parse_weibo_timestamp(data.get("created_at")),
        pics=_pics(data),
        video=_video(data, ""),
        retweeted=retweeted,
        region=region or author.location,
        reposts_count=_int(data.get("reposts_count")),
        comments_count=_int(data.get("comments_count")),
        attitudes_count=_int(data.get("attitudes_count")),
        is_long_text=bool(data.get("isLongText")),
    )


def decode_long_text(payload: object) -> str:
    data = _dict(payload)
    if isinstance(data.get("data"), dict):
        data = _dict(data["data"])
    return _string(data.get("longTextContent") or data.get("longTextContent_raw"))


def decode_comments(payload: object) -> tuple[list[dict[str, object]], int]:
    data = _dict(_dict(payload).get("data"))
    return (
        [_dict(item) for item in _list(data.get("data")) if isinstance(item, dict)],
        _int(data.get("total_number")),
    )
