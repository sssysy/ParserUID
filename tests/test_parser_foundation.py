"""ParserUID 解析器基础设施与注册中心离线测试。"""

from __future__ import annotations

import re
import sys
import json
import importlib.util
from typing import ClassVar
from pathlib import Path

import pytest

from gsuid_core.bot import Bot, _Bot
from ParserUID.parser import ParserRuntime
from ParserUID.sender import ContentItem
from gsuid_core.models import Event
from ParserUID.parsers.base import BaseParser, handle
from ParserUID.utils.models import Platform, TextContent, ImageContent
from ParserUID.parsers.config import (
    ParserManifest,
    load_parser_user_config,
    save_parser_user_config,
)
from ParserUID.utils.registry import (
    RegistrySnapshot,
    load_registry,
)
from ParserUID.utils.exceptions import ParserConfigException


class DummySampleParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="dummy", display_name="测试平台")

    @handle("dummy.com/video/", r"dummy\.com/video/(?P<id>\d+)")
    async def parse_video(self, searched: re.Match[str]) -> list[ContentItem]:
        video_id = searched.group("id")
        await self.send_info(title=f"视频标题_{video_id}", text="视频简介")
        return self.result(
            [
                self.create_text_content(f"正文_{video_id}"),
                self.create_image_content(path=Path("/tmp/fake.png")),
            ]
        )


class PropertyBackedParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="property_test", display_name="属性测试")

    @property
    def client(self) -> object:
        raise AssertionError("处理器发现不得执行 property")

    @handle("property.example/video/", r"property\.example/video/(?P<id>\d+)")
    async def parse_video(self, searched: re.Match[str]) -> list[ContentItem]:
        return [self.create_text_content(searched.group("id"))]


def test_parser_manifest_validation() -> None:
    valid_data: dict[str, object] = {
        "schema_version": 1,
        "name": "sample",
        "display_name": "示例平台",
        "default_enabled": False,
        "fields": {
            "use_proxy": {"type": "bool", "label": "代理", "default": False},
            "quality": {
                "type": "select",
                "label": "清晰度",
                "options": ["1080p", "720p"],
                "default": "1080p",
            },
            "tags": {"type": "list", "label": "标签", "default": ["a", "b"]},
        },
    }
    manifest = ParserManifest.from_dict(valid_data)
    assert manifest.name == "sample"
    assert manifest.display_name == "示例平台"
    assert manifest.default_enabled is False
    assert len(manifest.fields) == 3

    invalid_version = dict(valid_data, schema_version=2)
    with pytest.raises(ParserConfigException):
        ParserManifest.from_dict(invalid_version)

    invalid_default_enabled = dict(valid_data, default_enabled=True)
    with pytest.raises(ParserConfigException):
        ParserManifest.from_dict(invalid_default_enabled)


def test_parser_user_config_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ParserUID.parsers.config.get_user_config_path",
        lambda name: tmp_path / f"{name}.json",
    )
    manifest = ParserManifest(
        name="test_persist",
        display_name="测试持久化",
        fields={},
    )
    # 首次加载应生成默认关闭配置
    cfg = load_parser_user_config(manifest)
    assert cfg.enabled is False

    # 手动启用并保存
    cfg.enabled = True
    save_parser_user_config(cfg)

    # 再次加载必须保留已启用的状态
    reloaded = load_parser_user_config(manifest)
    assert reloaded.enabled is True


@pytest.mark.anyio
async def test_base_parser_execution() -> None:
    parser = DummySampleParser()
    specs = DummySampleParser.get_registered_specs()
    assert len(specs) == 1
    assert specs[0][0] == "dummy.com/video/"

    searched = specs[0][1].search("https://dummy.com/video/12345")
    assert searched is not None

    captured_cards: list[object] = []

    async def sink(card: object) -> None:
        captured_cards.append(card)

    result = await parser.parse("dummy.com/video/", searched, desc_sink=sink)
    assert len(captured_cards) == 1
    assert getattr(captured_cards[0], "title") == "视频标题_12345"

    assert len(result) == 2
    assert isinstance(result[0], TextContent)
    assert result[0].text() == "正文_12345"
    assert isinstance(result[1], ImageContent)

    await parser.close()


def test_registry_snapshot_matching() -> None:
    parser_short = DummySampleParser()
    pattern_short = re.compile(r"dummy\.com/v/(?P<id>\d+)")

    parser_long = DummySampleParser()
    pattern_long = re.compile(r"dummy\.com/video/(?P<id>\d+)")

    # 模拟按关键词长度降序排序
    rules = [
        ("dummy.com/video/", pattern_long, parser_long),
        ("dummy.com/v/", pattern_short, parser_short),
    ]
    snapshot = RegistrySnapshot(
        parsers={"long": parser_long, "short": parser_short},
        rules=rules,
    )

    matched = snapshot.match("https://dummy.com/video/999")
    assert matched is not None
    assert matched.keyword == "dummy.com/video/"
    assert matched.searched.group("id") == "999"

    no_match = snapshot.match("https://other.com/post/1")
    assert no_match is None


@pytest.mark.anyio
async def test_dispatch_logs_parser_hit_without_full_url(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    parser = DummySampleParser()
    pattern = dict(parser.get_registered_specs())["dummy.com/video/"]
    snapshot = RegistrySnapshot(
        parsers={"dummy": parser},
        rules=[("dummy.com/video/", pattern, parser)],
    )
    runtime = ParserRuntime(snapshot)
    matched = snapshot.match("https://dummy.com/video/12345")
    assert matched is not None
    monkeypatch.setattr(parser, "parse", lambda *_args, **_kwargs: [])

    event = Event(
        bot_id="bot",
        user_id="user",
        group_id="group",
        msg_id="message",
        raw_text="https://dummy.com/video/12345?secret=value",
    )
    bot = Bot(_Bot("bot"), event)

    with caplog.at_level("INFO", logger="GsCore"):
        try:
            assert await runtime.dispatch(bot, event) is True
        finally:
            await runtime.close()

    assert any(
        "链接命中，开始解析" in record.message
        and "parser=dummy.com/video/" in record.message
        and "host=dummy.com" in record.message
        for record in caplog.records
    )
    assert all("secret=value" not in record.message for record in caplog.records)


def test_handler_discovery_does_not_evaluate_properties() -> None:
    parser = PropertyBackedParser()

    assert list(parser._handlers) == ["property.example/video/"]


@pytest.mark.anyio
async def test_registry_enables_property_backed_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin_parsers = tmp_path / "builtin"
    thirdparty_parsers = tmp_path / "thirdparty"
    user_configs = tmp_path / "user_configs"
    builtin_parsers.mkdir()
    thirdparty_parsers.mkdir()
    user_configs.mkdir()

    site_dir = builtin_parsers / "property_test"
    site_dir.mkdir()
    (site_dir / "config.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "property_test",
                "display_name": "属性测试",
                "default_enabled": False,
                "fields": {},
            }
        ),
        encoding="utf-8",
    )
    (site_dir / "__init__.py").write_text(
        "from tests.test_parser_foundation import PropertyBackedParser\nPARSER = PropertyBackedParser\n",
        encoding="utf-8",
    )
    (user_configs / "property_test.json").write_text(
        json.dumps({"schema_version": 1, "enabled": True, "values": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("ParserUID.utils.registry.get_builtin_parsers_dir", lambda: builtin_parsers)
    monkeypatch.setattr("ParserUID.utils.registry.get_thirdparty_parsers_dir", lambda: thirdparty_parsers)
    monkeypatch.setattr("ParserUID.parsers.config.get_user_config_path", lambda name: user_configs / f"{name}.json")

    snapshot = await load_registry()
    matched = snapshot.match("https://property.example/video/42")
    await snapshot.close()

    assert matched is not None
    assert matched.searched.group("id") == "42"


@pytest.mark.anyio
async def test_registry_scanning_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builtin_parsers = tmp_path / "builtin"
    thirdparty_parsers = tmp_path / "thirdparty"
    user_configs = tmp_path / "user_configs"

    builtin_parsers.mkdir(parents=True)
    thirdparty_parsers.mkdir(parents=True)
    user_configs.mkdir(parents=True)

    monkeypatch.setattr("ParserUID.utils.registry.get_builtin_parsers_dir", lambda: builtin_parsers)
    monkeypatch.setattr("ParserUID.utils.registry.get_thirdparty_parsers_dir", lambda: thirdparty_parsers)
    monkeypatch.setattr("ParserUID.parsers.config.get_user_config_path", lambda name: user_configs / f"{name}.json")

    # 创建一个内置解析器 mock_site
    site_dir = builtin_parsers / "mock_site"
    site_dir.mkdir()
    (site_dir / "config.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "mock_site",
                "display_name": "测试站点",
                "default_enabled": False,
                "fields": {},
            }
        ),
        encoding="utf-8",
    )
    (site_dir / "__init__.py").write_text(
        "from tests.test_parser_foundation import DummySampleParser\n"
        "class MockSiteParser(DummySampleParser):\n"
        "    from ParserUID.utils.models import Platform\n"
        "    platform = Platform(name='mock_site', display_name='测试站点')\n"
        "PARSER = MockSiteParser\n",
        encoding="utf-8",
    )

    # 默认未启用，快照应为空
    snapshot_disabled = await load_registry()
    assert snapshot_disabled.match("https://dummy.com/video/100") is None

    # 手动在用户配置中开启 mock_site
    (user_configs / "mock_site.json").write_text(
        json.dumps({"schema_version": 1, "enabled": True, "values": {}}),
        encoding="utf-8",
    )

    # 重新加载快照，应能成功匹配
    snapshot_enabled = await load_registry()
    matched = snapshot_enabled.match("https://dummy.com/video/100")
    assert matched is not None
    assert matched.keyword == "dummy.com/video/"
    assert matched.searched.group("id") == "100"

    await snapshot_enabled.close()


@pytest.mark.anyio
async def test_media_segment_file_uri(tmp_path: Path) -> None:
    from ParserUID.sender import _media_segment, _build_segments
    from ParserUID.utils.models import FileContent, AudioContent, VideoContent

    fake_video = tmp_path / "test.mp4"
    fake_video.write_bytes(b"fake_video_bytes")
    fake_audio = tmp_path / "test.mp3"
    fake_audio.write_bytes(b"fake_audio_bytes")
    fake_file = tmp_path / "test.zip"
    fake_file.write_bytes(b"fake_file_bytes")

    # 1. 验证视频直接走 file:// 且不读内存 base64
    msg_video = _media_segment(fake_video, "video")
    assert msg_video.type == "video"
    assert str(msg_video.data).startswith("file://")
    assert "fake_video_bytes" not in str(msg_video.data)

    # 2. 验证音频直接走 record + file://
    msg_audio = _media_segment(fake_audio, "audio")
    assert msg_audio.type == "record"
    assert str(msg_audio.data).startswith("file://")

    # 3. 验证文件直接走 文件名|file:// 格式
    msg_file = _media_segment(fake_file, "file")
    assert msg_file.type == "file"
    assert str(msg_file.data).startswith(f"test.zip|{fake_file.resolve().as_uri()}")

    # 4. 验证自定义文件名
    msg_custom_file = _media_segment(fake_file, "file", file_name="rename.zip")
    assert str(msg_custom_file.data).startswith(f"rename.zip|{fake_file.resolve().as_uri()}")

    # 5. 验证端到端 _build_segments
    contents = [
        VideoContent(path=fake_video),
        AudioContent(path=fake_audio),
        FileContent(path=fake_file, name="custom.bin"),
    ]
    segments = await _build_segments(contents, show_failure_tip=True)
    assert len(segments) == 3
    assert segments[0].type == "video"
    assert segments[1].type == "record"
    assert segments[2].type == "file"
    assert str(segments[2].data).startswith("custom.bin|file://")


def test_all_24_migrated_parsers_registration() -> None:
    """验证全部 24 个迁移后的解析器均符合身份、契约与关键词无冲突规范。"""
    from ParserUID.parsers.base import BaseParser
    from ParserUID.utils.registry import (
        _import_parser_class,
        _load_manifest_from_dir,
        get_builtin_parsers_dir,
    )

    builtin_dir = get_builtin_parsers_dir()
    parser_dirs = sorted(
        [p for p in builtin_dir.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))],
        key=lambda x: x.name,
    )
    assert len(parser_dirs) == 24, f"期望 24 个内置解析器，实际发现 {len(parser_dirs)}"

    seen_keywords: dict[str, str] = {}

    for p in parser_dirs:
        manifest = _load_manifest_from_dir(p)
        assert manifest is not None, f"解析器 {p.name} 缺少或无效 config.json"
        assert manifest.default_enabled is False, f"解析器 {p.name} default_enabled 必须为 False"
        assert manifest.name == p.name, f"解析器 {p.name} manifest 名称不一致"

        parser_cls = _import_parser_class(p, f"ParserUID.parsers.{p.name}")
        assert parser_cls is not None, f"解析器 {p.name} 导入失败或未导出 PARSER"
        assert issubclass(parser_cls, BaseParser), f"解析器 {p.name} 未继承 BaseParser"
        assert parser_cls.platform.name == p.name, f"解析器 {p.name} platform.name 与目录名不一致"

        specs = parser_cls.get_registered_specs()
        assert len(specs) > 0, f"解析器 {p.name} 未注册任何 @handle 规则"

        for kw, _ in specs:
            assert kw not in seen_keywords, f"关键词 {kw} 在 {seen_keywords.get(kw)} 和 {p.name} 间发生冲突"
            seen_keywords[kw] = p.name


def test_xiaoheihe_parser_instantiates_without_proxy() -> None:
    """小黑盒启用时不得因缺少代理参数导致注册表加载失败。"""
    from ParserUID.parsers.xiaoheihe import XiaoheiheParser

    parser = XiaoheiheParser()

    assert parser.client.proxy is None


def test_nested_plugin_import_uses_relative_package_name() -> None:
    """框架以 plugins.ParserUID.ParserUID 加载时，内部不能依赖顶层 ParserUID。"""
    plugin_root = Path(__file__).resolve().parent.parent
    package_dir = plugin_root / "ParserUID"
    module_name = "plugins.ParserUID.ParserUID"
    stale = [
        key
        for key in sys.modules
        if key == "ParserUID" or key.startswith("ParserUID.") or key == module_name or key.startswith(f"{module_name}.")
    ]
    saved = {key: sys.modules.pop(key) for key in stale}
    sys.path.append(str(plugin_root.parent.parent))
    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            package_dir / "__init__.py",
            submodule_search_locations=[str(package_dir)],
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        runtime = importlib.import_module(f"{module_name}.render.runtime")
        _, manifest, environment = runtime.load_skin_pack("default")
        assert callable(module.parser.on_parser_message)
        template = environment.get_template(manifest.templates["common"])
        assert template.name == "common.html"
    finally:
        for key in [
            key
            for key in sys.modules
            if key == "ParserUID"
            or key.startswith("ParserUID.")
            or key == module_name
            or key.startswith(f"{module_name}.")
        ]:
            del sys.modules[key]
        sys.modules.update(saved)
