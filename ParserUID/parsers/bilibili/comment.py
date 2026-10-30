from __future__ import annotations

import re

import msgspec

from ...utils.utils import format_num
from ...render.models import Comment, CommentReply

_EMOTE_RE = re.compile(r"\[[^\[\]\n]{1,32}\]")
_LOCATION_PREFIX = "IP属地："


def comment_limit(value: object) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 5
    return max(0, min(limit, 20))


class BiliCommentMember(msgspec.Struct):
    mid: str = ""
    uname: str = ""
    name: str = ""
    avatar: str = ""
    face: str = ""


class BiliCommentContent(msgspec.Struct):
    message: str = ""
    emote: dict[str, BiliEmote] | None = None
    pictures: list[BiliPicture] | None = None


class BiliEmote(msgspec.Struct):
    text: str = ""


class BiliPicture(msgspec.Struct):
    img_src: str = ""


class BiliReplyControl(msgspec.Struct):
    location: str = ""
    time_desc: str = ""
    sub_reply_entry_text: str = ""


class BiliCommentItem(msgspec.Struct):
    rpid: int | str = 0
    id: int | str = 0
    like: int | str = 0
    count: int | str = 0
    rcount: int | str = 0
    ctime: int | str = 0
    member: BiliCommentMember = msgspec.field(default_factory=BiliCommentMember)
    content: BiliCommentContent = msgspec.field(default_factory=BiliCommentContent)
    replies: list["BiliCommentItem"] | None = None
    reply_control: BiliReplyControl = msgspec.field(default_factory=BiliReplyControl)


class BiliCommentData(msgspec.Struct):
    replies: list[BiliCommentItem] | None = None
    top_replies: list[BiliCommentItem] | None = None
    top: BiliCommentTop | None = None


class BiliCommentTop(msgspec.Struct):
    upper: BiliCommentItem | None = None


class BiliCommentReply(msgspec.Struct):
    code: int = 0
    message: str = ""
    data: BiliCommentData | None = None


def decode_comment_payload(payload: dict[str, object]) -> BiliCommentData:
    if "code" not in payload and "replies" in payload:
        # 兼容旧版 grpc-gateway 的裸 data 响应结构。
        return msgspec.convert(payload, type=BiliCommentData)
    reply = msgspec.convert(payload, type=BiliCommentReply)
    if reply.code != 0:
        raise ValueError(reply.message or "B站评论接口返回错误")
    return reply.data or BiliCommentData()


def decode_comments(
    payload: dict[str, object],
    *,
    limit: int,
) -> list[Comment]:
    data = decode_comment_payload(payload)
    roots: list[BiliCommentItem] = []
    seen: set[str] = set()

    if data.top is not None and data.top.upper is not None:
        _append_unique(roots, data.top.upper, seen)
    for item in (*(data.top_replies or ()), *(data.replies or ())):
        _append_unique(roots, item, seen)

    return [_to_card_comment(item) for item in roots[: max(0, limit)]]


def _append_unique(
    output: list[BiliCommentItem],
    item: BiliCommentItem,
    seen: set[str],
) -> bool:
    key = str(item.rpid or item.id or "")
    if not key or key in seen:
        return False
    seen.add(key)
    output.append(item)
    return True


def _location(control: BiliReplyControl) -> str | None:
    value = control.location.strip()
    if not value:
        return None
    return value.removeprefix(_LOCATION_PREFIX).strip() or None


def _number(value: int | str | None) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str):
        try:
            return max(0, int(float(value)))
        except ValueError:
            return 0
    return 0


def _to_card_comment(
    item: BiliCommentItem,
) -> Comment:
    avatar_url = item.member.avatar or item.member.face or None
    location = item.reply_control.location
    if location.startswith(_LOCATION_PREFIX):
        location = location[len(_LOCATION_PREFIX) :].strip()

    replies: list[CommentReply] = []
    for reply_item in item.replies or ():
        reply_avatar = reply_item.member.avatar or reply_item.member.face or None
        replies.append(
            CommentReply(
                author_name=reply_item.member.uname or reply_item.member.name or "B站用户",
                text=reply_item.content.message,
                avatar_url=reply_avatar,
            )
        )

    return Comment(
        author_name=item.member.uname or item.member.name or "B站用户",
        text=item.content.message,
        avatar_url=avatar_url,
        location=location or None,
        timestamp=str(item.ctime) if item.ctime else None,
        like_count=format_num(int(item.like or 0)),
        reply_count=format_num(int(item.count or item.rcount or 0)),
        replies=tuple(replies),
    )
