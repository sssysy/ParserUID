# 解析器配置参考

## 源码默认配置

每个解析器包的 `config.json` 使用以下结构：

```json
{
  "schema_version": 1,
  "name": "example",
  "display_name": "示例平台",
  "default_enabled": false,
  "fields": {
    "use_proxy": {
      "type": "bool",
      "label": "使用代理",
      "default": false
    },
    "cookies": {
      "type": "text",
      "label": "Cookies",
      "hint": "选填",
      "secret": true,
      "default": ""
    },
    "quality": {
      "type": "select",
      "label": "画质",
      "options": ["auto", "1080p", "720p"],
      "default": "auto"
    }
  }
}
```

支持字段类型：

- `bool`
- `int`
- `float`
- `string`
- `text`
- `select`
- `list`

`list` 的默认值必须是字符串列表。注册中心在加载解析器前校验名称、
schema 版本、字段类型和默认值；定义无效时拒绝该解析器。

## 用户配置

首次发现解析器时，注册中心创建：

```text
data/ParserUID/parser_config/<name>.json
```

结构：

```json
{
  "schema_version": 1,
  "enabled": false,
  "values": {
    "use_proxy": false,
    "cookies": "",
    "quality": "auto"
  }
}
```

用户值不得写回源码目录，也不得另设第二个解析器启用开关。

所有解析器默认关闭：

- 源码 `config.json` 的 `default_enabled` 必须为 `false`。
- 新增解析器和修改现有解析器都不得改为默认启用。
- 默认值只用于首次生成用户配置。
- 已有 `parser_config/<name>.json` 中的 `enabled` 不得被默认值覆盖。

## 读取配置

解析器通过基类提供的平台配置读取声明字段：

```python
quality = self.mycfg.quality
cookies = self.mycfg.cookies
```

不要在解析器各模块散落 `config.parser.<name>` 查询，
也不要读取未在 `config.json` 声明的字段。

配置字段必须在类型上可追踪。`select` 字段应使用明确的枚举或受控字面量集合，
不得用任意字符串加隐藏别名兼容层。

## 网页配置页

解析器启停和皮肤包选择由插件网页配置页负责。
其他阈值，包括合并转发阈值、并发、超时、大小和时长限制，
放在插件普通配置中。

解析器页面职责：

- 列出内置和第三方解析器。
- 显示解析器名称、来源、启用状态和声明字段。
- 保存单个解析器的 `enabled` 与字段值。
- 对 `secret: true` 字段遮蔽原值。
- 页面提交空敏感字段时保留已存值。
- 保存后刷新注册快照。

页面使用 GsCore 的 `register_plugin_page()` 和 `PluginAPI()`，
API 路径位于 `/api/<plugin_id>` 下，默认要求登录鉴权。

页面不得：

- 读取或回显敏感字段原值。
- 接受任意文件路径作为解析器或皮肤包入口。
- 硬编码某个平台的配置字段。
- 伪造 Python 模块热卸载成功。

## 新增字段

新增解析器字段时依次完成：

1. 在解析器 `config.json` 添加字段、类型、标签和合法默认值。
2. 在解析器中使用 `self.mycfg.<字段>`。
3. 凭证字段设置 `"secret": true`。
4. 检查旧用户配置缺少该字段时的默认值行为。
5. 补充聚焦配置解析或注册逻辑的测试。
6. 校验 JSON。

不得把解析器字段写入插件全局配置或全局 `config.json`。

## 生效时机

- 保存启停状态或参数后刷新解析器配置快照。
- 新增或删除解析器目录、修改 Python 代码后，需要重新加载插件。
- 注册中心负责解析器实例生命周期。
- 被替换或停用的解析器必须关闭其异步会话和长生命周期资源。
- 第三方模块已导入且无法安全卸载时，明确提示需要重启 Core。
