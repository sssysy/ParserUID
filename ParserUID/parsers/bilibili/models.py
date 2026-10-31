from __future__ import annotations

from dataclasses import field, dataclass

import msgspec
from msgspec import Struct, field as msfield

from ...utils.utils import format_num
from ...render.models import Metric


class BiliUserLikes(Struct):
    like_num: int = 0


def _count(value: object) -> str:
    if isinstance(value, bool):
        return format_num(int(value))
    if isinstance(value, int | float | str):
        try:
            return format_num(int(value or 0))
        except (TypeError, ValueError):
            return "0"
    return "0"


class BiliOwner(Struct):
    mid: int = 0
    name: str = ""
    face: str = ""


class BiliStat(Struct):
    view: int = 0
    danmaku: int = 0
    reply: int = 0
    favorite: int = 0
    coin: int = 0
    share: int = 0
    like: int = 0

    @property
    def metrics(self) -> list[Metric]:
        return [
            Metric(label="播放", value=_count(self.view)),
            Metric(label="点赞", value=_count(self.like)),
            Metric(label="评论", value=_count(self.reply)),
            Metric(label="收藏", value=_count(self.favorite)),
            Metric(label="分享", value=_count(self.share)),
            Metric(label="投币", value=_count(self.coin)),
            Metric(label="弹幕", value=_count(self.danmaku)),
        ]


class BiliPage(Struct):
    cid: int = 0
    page: int = 1
    part: str = ""
    duration: int = 0
    first_frame: str | None = None
    ctime: int = 0


class BiliVideoInfo(Struct):
    bvid: str = ""
    aid: int = 0
    title: str = ""
    desc: str = ""
    pic: str = ""
    pubdate: int = 0
    ctime: int = 0
    duration: int = 0
    owner: BiliOwner = msfield(default_factory=BiliOwner)
    stat: BiliStat = msfield(default_factory=BiliStat)
    pages: list[BiliPage] = msfield(default_factory=list)
    cid: int = 0

    @property
    def cover_url(self) -> str:
        if self.pic.startswith("//"):
            return f"https:{self.pic}"
        if self.pic.startswith("http://"):
            return f"https://{self.pic[7:]}"
        return self.pic


class BiliVideoView(Struct):
    code: int = 0
    message: str = ""
    data: BiliVideoInfo | None = None


class BiliFavoriteUpper(Struct):
    mid: int = 0
    name: str = ""
    face: str = ""


class BiliFavoriteInfo(Struct):
    title: str = ""
    cover: str = ""
    upper: BiliFavoriteUpper = msfield(default_factory=BiliFavoriteUpper)
    ctime: int = 0
    mtime: int = 0
    media_count: int = 0
    intro: str = ""


class BiliFavoriteMedia(Struct):
    id: int = 0
    type: int = 0
    title: str = ""
    cover: str = ""
    intro: str = ""
    duration: int = 0
    bvid: str = ""
    link: str = ""
    upper: BiliFavoriteUpper = msfield(default_factory=BiliFavoriteUpper)

    @property
    def url(self) -> str:
        if self.bvid:
            return f"https://www.bilibili.com/video/{self.bvid}"
        if self.link:
            return self.link.replace("bilibili://video/", "https://bilibili.com/video/av")
        return ""


class BiliFavoriteData(Struct):
    info: BiliFavoriteInfo = msfield(default_factory=BiliFavoriteInfo)
    medias: list[BiliFavoriteMedia] | None = None
    has_more: bool = False

    @property
    def metrics(self) -> list[Metric]:
        return [
            Metric(label="收藏", value=_count(self.info.media_count)),
            Metric(label="视频", value=_count(len(self.medias or ()))),
        ]


class BiliFavoriteReply(Struct):
    code: int = 0
    message: str = ""
    data: BiliFavoriteData | None = None


class BiliBangumiStat(Struct):
    views: int = 0
    danmakus: int = 0
    reply: int = 0
    favorite: int = 0
    coins: int = 0
    share: int = 0
    likes: int = 0

    @property
    def metrics(self) -> list[Metric]:
        return [
            Metric(label="播放", value=_count(self.views)),
            Metric(label="点赞", value=_count(self.likes)),
            Metric(label="追番", value=_count(self.favorite)),
            Metric(label="评论", value=_count(self.reply)),
            Metric(label="投币", value=_count(self.coins)),
            Metric(label="分享", value=_count(self.share)),
            Metric(label="弹幕", value=_count(self.danmakus)),
        ]


class BiliBangumiData(Struct):
    season_id: int = 0
    title: str = ""
    season_title: str = ""
    cover: str = ""
    square_cover: str = ""
    evaluate: str = ""
    share_url: str = ""
    stat: BiliBangumiStat = msfield(default_factory=BiliBangumiStat)


class BiliBangumiReply(Struct):
    code: int = 0
    message: str = ""
    data: BiliBangumiData | None = None
    result: BiliBangumiData | None = None


class BiliLiveData(Struct):
    uid: int = 0
    room_id: int = 0
    short_id: int = 0
    attention: int = 0
    online: int = 0
    is_portrait: bool = False
    description: str = ""
    live_status: int = 0
    area_name: str = ""
    parent_area_name: str = ""
    cover: str = ""
    title: str = ""
    user_cover: str = ""
    keyframe: str = ""
    live_time: str = ""


class BiliLiveReply(Struct):
    code: int = 0
    message: str = ""
    data: BiliLiveData | None = None


class BiliUserCard(Struct):
    mid: str = ""
    name: str = ""
    face: str = ""
    sign: str = ""
    fans: int = 0
    attention: int = 0
    follower: int = 0
    likes: BiliUserLikes | None = None

    @property
    def metrics(self) -> list[Metric]:
        liked = self.likes.like_num if self.likes is not None else 0
        followers = self.fans or self.follower
        return [
            Metric(label="获赞", value=_count(liked)),
            Metric(label="关注", value=_count(self.attention)),
            Metric(label="粉丝", value=_count(followers)),
        ]


class BiliUserSpaceData(Struct):
    card: BiliUserCard = msfield(default_factory=BiliUserCard)
    follower: int = 0
    like_num: int = 0


class BiliUserSpaceReply(Struct):
    code: int = 0
    data: BiliUserSpaceData | None = None


@dataclass(slots=True)
class BiliDynamicAuthor:
    mid: str = ""
    name: str = ""
    face: str = ""
    sign: str = ""
    timestamp: int | None = None


@dataclass(slots=True)
class BiliDynamicContent:
    url: str
    title: str | None = None
    text: str | None = None
    author: BiliDynamicAuthor = field(default_factory=BiliDynamicAuthor)
    images: list[str] = field(default_factory=list)
    cover: str | None = None
    metrics: list[Metric] = field(default_factory=list)
    orig: BiliDynamicContent | None = None


def decode_video(payload: object) -> BiliVideoInfo:
    reply = msgspec.convert(payload, type=BiliVideoView)
    if reply.code != 0 or reply.data is None:
        raise ValueError(reply.message or "视频信息为空")
    return reply.data


def decode_favorite(payload: object) -> BiliFavoriteData:
    reply = msgspec.convert(payload, type=BiliFavoriteReply)
    if reply.code != 0 or reply.data is None:
        raise ValueError(reply.message or "收藏夹信息为空")
    return reply.data


def decode_bangumi(payload: object) -> BiliBangumiData:
    reply = msgspec.convert(payload, type=BiliBangumiReply)
    bangumi = reply.data or reply.result
    if reply.code != 0 or bangumi is None:
        raise ValueError(reply.message or "番剧信息为空")
    return bangumi


def decode_live(payload: object) -> BiliLiveData:
    reply = msgspec.convert(payload, type=BiliLiveReply)
    if reply.code != 0 or reply.data is None:
        raise ValueError(reply.message or "直播间信息为空")
    return reply.data


def decode_user(payload: object) -> BiliUserCard | None:
    try:
        reply = msgspec.convert(payload, type=BiliUserSpaceReply)
    except (msgspec.ValidationError, TypeError, ValueError):
        return None
    if reply.code != 0 or reply.data is None:
        return None
    card = reply.data.card
    if card.fans <= 0:
        card.fans = reply.data.follower
    if card.likes is None and reply.data.like_num:
        card.likes = BiliUserLikes(like_num=reply.data.like_num)
    return card
