"""酷安镜像页面响应模型。"""

from __future__ import annotations

from msgspec import Struct, field


class CoolapkFeed(Struct):
    """镜像站动态页中的主帖数据。"""

    feed_type: str = field(name="feedType", default="")
    message_title: str = ""
    title: str = ""
    username: str = ""
    user_avatar: str = field(name="userAvatar", default="")
    dateline: int | None = None
    message: str = ""
    pic_arr: list[str] | None = field(name="picArr", default=None)


class CoolapkFeedPageProps(Struct):
    """动态页的 pageProps。"""

    feed: CoolapkFeed | None = None
    id: str = ""
    ai_summary: str | None = field(name="aiSummary", default=None)


class CoolapkFeedProps(Struct):
    """动态页的 Next.js props。"""

    page_props: CoolapkFeedPageProps = field(name="pageProps", default_factory=CoolapkFeedPageProps)


class CoolapkFeedPage(Struct):
    """酷安动态页 __NEXT_DATA__ 的根结构。"""

    props: CoolapkFeedProps = field(default_factory=CoolapkFeedProps)


class CoolapkReplyRow(Struct):
    """根评论下展示的一条一级回复。"""

    username: str = ""
    rusername: str | None = None
    dateline: int | None = None
    message: str = ""
    pic_arr: list[str] | None = field(name="picArr", default=None)


class CoolapkComment(Struct):
    """镜像站返回的一条根评论。"""

    username: str = ""
    user_avatar: str = field(name="userAvatar", default="")
    dateline: int | None = None
    likenum: int | None = 0
    replynum: int | None = 0
    message: str = ""
    pic_arr: list[str] | None = field(name="picArr", default=None)
    reply_rows: list[CoolapkReplyRow] = field(name="replyRows", default_factory=list)


class CoolapkReplyPageProps(Struct):
    """评论页的 pageProps。"""

    replies: list[CoolapkComment] = field(default_factory=list)


class CoolapkReplyProps(Struct):
    """评论页的 Next.js props。"""

    page_props: CoolapkReplyPageProps = field(name="pageProps", default_factory=CoolapkReplyPageProps)


class CoolapkReplyPage(Struct):
    """酷安评论页 __NEXT_DATA__ 的根结构。"""

    props: CoolapkReplyProps = field(default_factory=CoolapkReplyProps)
