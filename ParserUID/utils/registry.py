"""解析器目录扫描、Manifest 校验、防冲突与只读匹配快照。"""

from __future__ import annotations

import re
import sys
import json
import asyncio
import importlib.util
from typing import TypeAlias
from pathlib import Path

from gsuid_core.logger import logger

from ..config import THIRDPARTY_PARSERS_DIR
from ..parsers.base import BaseParser
from ..parsers.config import (
    ParserManifest,
    load_parser_user_config,
)

RuleTuple: TypeAlias = tuple[str, re.Pattern[str], BaseParser]
LOGO_FILENAME = "logo.png"
THIRDPARTY_MODULE_PREFIX = "parseruid_thirdparty"


class ParserMatchResult:
    """关键词与正则命中结果的契约实现。"""

    def __init__(
        self,
        keyword: str,
        searched: re.Match[str],
        parser: BaseParser,
    ) -> None:
        self.keyword = keyword
        self._searched = searched
        self._parser = parser

    @property
    def searched(self) -> re.Match[str]:
        return self._searched

    @property
    def parser(self) -> BaseParser:
        return self._parser


class RegistrySnapshot:
    """解析器运行时只读快照，负责快速匹配与安全关闭。"""

    def __init__(
        self,
        parsers: dict[str, BaseParser],
        rules: list[RuleTuple],
    ) -> None:
        self._parsers = parsers
        self._rules = rules

    def match(self, url: str) -> ParserMatchResult | None:
        for keyword, pattern, parser in self._rules:
            if keyword in url:
                searched = pattern.search(url)
                if searched is not None:
                    return ParserMatchResult(
                        keyword=keyword,
                        searched=searched,
                        parser=parser,
                    )
        return None

    def get(self, name: str) -> BaseParser | None:
        """按平台名返回已启用的解析器实例。"""
        return self._parsers.get(name)

    async def close(self) -> None:
        tasks = [parser.close() for parser in self._parsers.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def get_builtin_parsers_dir() -> Path:
    """返回内置解析器源码根目录。"""
    return Path(__file__).parent.parent / "parsers"


def get_thirdparty_parsers_dir() -> Path:
    """返回第三方解析器数据根目录。"""
    path = THIRDPARTY_PARSERS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_manifest_from_dir(dir_path: Path) -> ParserManifest | None:
    config_file = dir_path / "config.json"
    if not config_file.is_file():
        return None
    try:
        raw_text = config_file.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        if not isinstance(data, dict):
            return None
        return ParserManifest.from_dict(data)
    except Exception as exc:
        logger.warning(
            f"[Parser·注册] 忽略无效配置 | dir={dir_path.name} | error={type(exc).__name__}",
        )
        return None


def _install_parseruid_namespace_alias() -> None:
    """把第三方包的 ParserUID 导入映射到当前实际加载的插件包。"""
    package_root = __package__.rpartition(".")[0]
    if package_root == "ParserUID" or package_root not in sys.modules:
        return
    sys.modules["ParserUID"] = sys.modules[package_root]
    prefix = f"{package_root}."
    for module_name, module in tuple(sys.modules.items()):
        if module_name.startswith(prefix):
            alias = f"ParserUID.{module_name.removeprefix(prefix)}"
            sys.modules[alias] = module


def _clear_thirdparty_module_cache(module_name: str) -> None:
    """热重载时清除第三方包旧模块，避免继续继承上一代 BaseParser。"""
    prefix = f"{module_name}."
    for loaded_name in tuple(sys.modules):
        if loaded_name == module_name or loaded_name.startswith(prefix):
            sys.modules.pop(loaded_name, None)


def _import_parser_class(
    dir_path: Path,
    module_name: str,
) -> type[BaseParser] | None:
    init_file = dir_path / "__init__.py"
    if not init_file.is_file():
        return None

    if module_name.startswith(THIRDPARTY_MODULE_PREFIX):
        _install_parseruid_namespace_alias()
        _clear_thirdparty_module_cache(module_name)

    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_file,
            submodule_search_locations=[str(dir_path)],
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.warning(
            f"[Parser·注册] 模块导入失败 | name={dir_path.name} | error={type(exc).__name__}",
        )
        return None

    parser_cls = getattr(module, "PARSER", None)
    if not isinstance(parser_cls, type) or not issubclass(parser_cls, BaseParser):
        logger.warning(f"[Parser·注册] PARSER 未继承 BaseParser | name={dir_path.name}")
        return None

    return parser_cls


def get_parser_logo_path(dir_path: Path) -> Path | None:
    """只接受解析器包根目录内的 logo.png。"""
    logo_path = dir_path / LOGO_FILENAME
    if not logo_path.is_file():
        return None
    try:
        resolved = logo_path.resolve(strict=True)
        root = dir_path.resolve(strict=True)
    except OSError as exc:
        logger.warning(
            f"[Parser·注册] logo 路径无效 | name={dir_path.name} | error={type(exc).__name__}",
        )
        return None
    if resolved.parent != root:
        logger.warning(f"[Parser·注册] logo 必须位于解析器包根目录 | name={dir_path.name}")
        return None
    return resolved


async def load_registry() -> RegistrySnapshot:
    """扫描内置与第三方目录，校验一致性与防冲突，生成已启用解析器的匹配快照。"""
    builtin_dir = get_builtin_parsers_dir()
    thirdparty_dir = get_thirdparty_parsers_dir()

    active_parsers: dict[str, BaseParser] = {}
    active_rules: list[RuleTuple] = []
    registered_keywords: dict[str, str] = {}
    discovered_names: set[str] = set()

    for is_thirdparty, root_dir in ((False, builtin_dir), (True, thirdparty_dir)):
        if not root_dir.is_dir():
            continue
        for child in sorted(root_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue

            manifest = _load_manifest_from_dir(child)
            if manifest is None:
                continue

            if is_thirdparty and child.name in discovered_names:
                logger.warning(f"[Parser·注册] 第三方解析器与内置重名被拒绝 | name={child.name}")
                continue

            if is_thirdparty:
                module_prefix = THIRDPARTY_MODULE_PREFIX
            else:
                module_prefix = f"{__package__.rpartition('.')[0]}.parsers"
            parser_cls = _import_parser_class(
                child,
                f"{module_prefix}.{child.name}",
            )
            if parser_cls is None:
                continue

            if child.name != manifest.name or child.name != parser_cls.platform.name:
                logger.warning(
                    f"[Parser·注册] 身份不一致被拒绝 | dir={child.name} "
                    f"| manifest={manifest.name} | platform={parser_cls.platform.name}",
                )
                continue

            specs = parser_cls.get_registered_specs()
            has_conflict = False
            for kw, _ in specs:
                if kw in registered_keywords:
                    logger.warning(
                        f"[Parser·注册] 关键词冲突被拒绝 | keyword={kw} "
                        f"| existing={registered_keywords[kw]} | candidate={child.name}",
                    )
                    has_conflict = True
                    break
            if has_conflict:
                continue

            discovered_names.add(child.name)
            for kw, _ in specs:
                registered_keywords[kw] = child.name

            user_config = load_parser_user_config(manifest)
            if not user_config.enabled:
                continue

            try:
                instance = parser_cls(config=user_config)
                instance.set_logo_path(get_parser_logo_path(child))
            except Exception as exc:
                detail = f" | attribute={exc.name}" if isinstance(exc, AttributeError) and exc.name else ""
                logger.warning(
                    f"[Parser·注册] 实例化失败被跳过 | name={child.name} | error={type(exc).__name__}{detail}",
                )
                continue
            active_parsers[child.name] = instance
            for kw, pattern in specs:
                active_rules.append((kw, pattern, instance))

    active_rules.sort(key=lambda item: len(item[0]), reverse=True)
    logger.info(
        f"[Parser·注册] 注册表加载完成 | 发现={len(discovered_names)} | 启用={len(active_parsers)}",
    )
    return RegistrySnapshot(active_parsers, active_rules)
