"""哔哩哔哩解析器请求兼容性测试。"""

from __future__ import annotations

import httpx
import pytest

from ParserUID.parsers.bilibili.parser import BROWSER_USER_AGENT, BilibiliParser


@pytest.mark.anyio
async def test_bilibili_requests_use_browser_user_agent() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"bvid": "BV1GJ411x7h7"}})

    parser = BilibiliParser()
    parser._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        payload = await parser._get_json(
            parser.API_VIEW,
            params={"bvid": "BV1GJ411x7h7"},
            error="获取视频信息失败",
        )
    finally:
        await parser.close()

    assert payload["code"] == 0
    assert requests[0].headers["User-Agent"] == BROWSER_USER_AGENT
    assert requests[0].headers["Referer"] == "https://www.bilibili.com/"


@pytest.mark.anyio
async def test_bilibili_video_falls_back_after_412(monkeypatch: pytest.MonkeyPatch) -> None:
    paths: list[str] = []

    async def skip_retry_delay(_: float) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/wbi/view"):
            return httpx.Response(412, text="risk control")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "bvid": "BV1GJ411x7h7",
                    "aid": 80433022,
                    "cid": 137649199,
                    "pages": [{"cid": 137649199, "page": 1, "part": "正片", "duration": 3}],
                },
            },
        )

    monkeypatch.setattr(
        "ParserUID.parsers.bilibili.parser.asyncio.sleep",
        skip_retry_delay,
    )
    parser = BilibiliParser()
    parser._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        info = await parser._fetch_video("BV1GJ411x7h7")
    finally:
        await parser.close()

    assert info.bvid == "BV1GJ411x7h7"
    assert paths == ["/x/web-interface/wbi/view", "/x/web-interface/view"]
