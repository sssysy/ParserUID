"""单个平台解析器的配置模式、Manifest 校验与持久化。"""

from __future__ import annotations

import re
import json
from typing import Literal, TypeAlias
from pathlib import Path
from dataclasses import field, dataclass

from gsuid_core.data_store import get_res_path

from ..utils.exceptions import ParserConfigException

ParserFieldType: TypeAlias = Literal["bool", "int", "float", "string", "text", "select", "list"]
ParserFieldValue: TypeAlias = bool | int | float | str | list[str]
AccessMode: TypeAlias = Literal["none", "white", "black"]

_GROUP_ID_RE = re.compile(r"^\d+$")


def _match_field_type(raw_type: str) -> ParserFieldType | None:
    match raw_type:
        case "bool" | "int" | "float" | "string" | "text" | "select" | "list":
            return raw_type
        case _:
            return None


@dataclass(frozen=True, slots=True)
class ParserFieldDefinition:
    """解析器声明的单个配置字段元信息。"""

    type: ParserFieldType
    label: str
    default: ParserFieldValue
    hint: str | None = None
    secret: bool = False
    options: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParserManifest:
    """解析器源码目录内 config.json 的只读元数据。"""

    name: str
    display_name: str
    schema_version: int = 1
    default_enabled: bool = False
    fields: dict[str, ParserFieldDefinition] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ParserManifest:
        """从原生字典反序列化并严格校验 Manifest 契约。"""
        schema_version = data.get("schema_version")
        if not isinstance(schema_version, int) or schema_version != 1:
            raise ParserConfigException("schema_version 必须为整数 1")

        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ParserConfigException("name 必须是非空字符串")

        display_name = data.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ParserConfigException("display_name 必须是非空字符串")

        default_enabled = data.get("default_enabled")
        if default_enabled is not False:
            raise ParserConfigException("所有解析器的 default_enabled 必须为 false")

        raw_fields = data.get("fields")
        if raw_fields is None:
            raw_fields = {}
        if not isinstance(raw_fields, dict):
            raise ParserConfigException("fields 必须是字典")

        parsed_fields: dict[str, ParserFieldDefinition] = {}
        for field_name, raw_field in raw_fields.items():
            if not isinstance(field_name, str) or not field_name.strip():
                raise ParserConfigException("字段名必须是非空字符串")
            if not isinstance(raw_field, dict):
                raise ParserConfigException(f"字段 {field_name} 定义必须是字典")
            parsed_fields[field_name] = _parse_field_definition(field_name, raw_field)

        return cls(
            name=name,
            display_name=display_name,
            schema_version=schema_version,
            default_enabled=False,
            fields=parsed_fields,
        )


def _parse_field_definition(name: str, raw: dict[str, object]) -> ParserFieldDefinition:
    raw_type = raw.get("type")
    if not isinstance(raw_type, str):
        raise ParserConfigException(f"字段 {name} 必须提供字符串类型的 type")

    field_type = _match_field_type(raw_type)
    if field_type is None:
        raise ParserConfigException(f"字段 {name} 的 type 无效：{raw_type}")

    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ParserConfigException(f"字段 {name} 必须提供非空 label")

    hint = raw.get("hint")
    if hint is not None and not isinstance(hint, str):
        raise ParserConfigException(f"字段 {name} 的 hint 必须为字符串或 null")

    secret = raw.get("secret", False)
    if not isinstance(secret, bool):
        raise ParserConfigException(f"字段 {name} 的 secret 必须为布尔值")

    raw_options = raw.get("options", ())
    options: tuple[str, ...] = ()
    if field_type == "select":
        if not isinstance(raw_options, (list, tuple)) or not raw_options:
            raise ParserConfigException(f"select 字段 {name} 必须提供非空 options")
        for opt in raw_options:
            if not isinstance(opt, str):
                raise ParserConfigException(f"select 字段 {name} 的选项必须是字符串")
        options = tuple(raw_options)

    default = raw.get("default")
    typed_default = _validate_field_value(name, field_type, default, options)

    return ParserFieldDefinition(
        type=field_type,
        label=label,
        default=typed_default,
        hint=hint,
        secret=secret,
        options=options,
    )


def _validate_field_value(
    name: str,
    field_type: str,
    value: object,
    options: tuple[str, ...] = (),
) -> ParserFieldValue:
    match field_type:
        case "bool":
            if not isinstance(value, bool):
                raise ParserConfigException(f"字段 {name} 期望 bool 类型")
            return value
        case "int":
            if type(value) is not int:
                raise ParserConfigException(f"字段 {name} 期望 int 类型")
            return value
        case "float":
            if isinstance(value, (int, float)) and type(value) is not bool:
                return float(value)
            raise ParserConfigException(f"字段 {name} 期望 float 类型")
        case "string" | "text":
            if not isinstance(value, str):
                raise ParserConfigException(f"字段 {name} 期望字符串类型")
            return value
        case "select":
            if not isinstance(value, str):
                raise ParserConfigException(f"字段 {name} 期望字符串类型")
            if options and value not in options:
                raise ParserConfigException(f"字段 {name} 的值不在 options 中：{value}")
            return value
        case "list":
            if not isinstance(value, list):
                raise ParserConfigException(f"字段 {name} 期望列表类型")
            for item in value:
                if not isinstance(item, str):
                    raise ParserConfigException(f"字段 {name} 的列表项必须是字符串")
            return list(value)
    raise ParserConfigException(f"未知的字段类型：{field_type}")


@dataclass
class ParserItemConfig:
    """运行时单个解析器的配置容器，提供类型安全的字段访问与 self.mycfg 属性访问。"""

    name: str
    enabled: bool
    values: dict[str, ParserFieldValue]
    access_mode: AccessMode = "none"
    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self.values.get(key)
        return val if isinstance(val, bool) else default

    def get_str(self, key: str, default: str = "") -> str:
        val = self.values.get(key)
        return val if isinstance(val, str) else default

    def get_int(self, key: str, default: int = 0) -> int:
        val = self.values.get(key)
        return val if type(val) is int else default

    def get_float(self, key: str, default: float = 0.0) -> float:
        val = self.values.get(key)
        if isinstance(val, (int, float)) and type(val) is not bool:
            return float(val)
        return default

    def get_list(self, key: str, default: list[str] | None = None) -> list[str]:
        val = self.values.get(key)
        return list(val) if isinstance(val, list) else (default or [])

    def allows_group(self, group_id: str | None) -> bool:
        """按当前名单模式判断群聊是否允许接收该解析器消息。"""
        if self.access_mode == "none":
            return True
        if not group_id:
            return False
        if self.access_mode == "white":
            return group_id in self.whitelist
        return group_id not in self.blacklist

    def __getattr__(self, name: str) -> ParserFieldValue:
        if name in self.values:
            return self.values[name]
        raise AttributeError(f"解析器 {self.name} 配置中无此字段：{name}")


def get_user_config_path(name: str) -> Path:
    """返回解析器用户配置文件的存储路径。"""
    return get_res_path("ParserUID") / "parser_config" / f"{name}.json"


def normalize_group_ids(raw: object) -> list[str]:
    """校验并去重群号列表，忽略非字符串和非数字项。"""
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        group_id = item.strip()
        if _GROUP_ID_RE.fullmatch(group_id) and group_id not in result:
            result.append(group_id)
    return result


def normalize_access_mode(raw: object) -> AccessMode:
    """收窄持久化的名单模式；未知值回落到不过滤。"""
    if raw == "white" or raw == "black":
        return raw
    return "none"


def load_parser_user_config(manifest: ParserManifest) -> ParserItemConfig:
    """读取或以默认值初始化解析器的用户配置，确保不会覆盖已存的启停状态。"""
    config_path = get_user_config_path(manifest.name)
    if not config_path.is_file():
        default_values = {k: v.default for k, v in manifest.fields.items()}
        cfg = ParserItemConfig(
            name=manifest.name,
            enabled=False,
            values=default_values,
        )
        save_parser_user_config(cfg)
        return cfg

    try:
        content = config_path.read_text(encoding="utf-8")
        raw = json.loads(content)
        if not isinstance(raw, dict):
            raise ValueError("配置内容必须是 JSON 对象")
    except Exception as exc:
        raise ParserConfigException(f"读取解析器配置失败 {manifest.name}: {exc}") from exc

    raw_enabled = raw.get("enabled", False)
    enabled = raw_enabled if isinstance(raw_enabled, bool) else False
    raw_values = raw.get("values", {})
    values_dict = raw_values if isinstance(raw_values, dict) else {}

    merged_values: dict[str, ParserFieldValue] = {}
    for field_name, field_def in manifest.fields.items():
        if field_name in values_dict:
            try:
                merged_values[field_name] = _validate_field_value(
                    field_name,
                    field_def.type,
                    values_dict[field_name],
                    field_def.options,
                )
            except ParserConfigException:
                merged_values[field_name] = field_def.default
        else:
            merged_values[field_name] = field_def.default

    access_mode = normalize_access_mode(raw.get("access_mode", "none"))
    whitelist = normalize_group_ids(raw.get("whitelist", []))
    blacklist = normalize_group_ids(raw.get("blacklist", []))

    return ParserItemConfig(
        name=manifest.name,
        enabled=enabled,
        values=merged_values,
        access_mode=access_mode,
        whitelist=whitelist,
        blacklist=blacklist,
    )


def save_parser_user_config(config: ParserItemConfig) -> None:
    """持久化保存单个解析器的用户配置。"""
    config_path = get_user_config_path(config.name)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "enabled": config.enabled,
        "values": config.values,
        "access_mode": config.access_mode,
        "whitelist": normalize_group_ids(config.whitelist),
        "blacklist": normalize_group_ids(config.blacklist),
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    config_path.write_text(content, encoding="utf-8")
