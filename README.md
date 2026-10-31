# ParserUID

<p align="center">
  <a href="ICON.png">
    <img src="ICON.png" width="256" height="256" alt="ParserUID">
  </a>
</p>
<h1 align="center">ParserUID 0.1.0</h1>
<h4 align="center">基于 gsuid_core 的多平台聚合解析插件</h4>

<p align="center">
  <a href="https://www.python.org/" target="_blank"><img src="https://img.shields.io/badge/Python-3.12+-blue" alt="Python 3.12+"></a>
  <a href="https://github.com/Genshin-bots/gsuid_core" target="_blank"><img src="https://img.shields.io/badge/gsuid_core-0.11.2+-orange" alt="gsuid_core 0.11.2+"></a>
</p>

<div align="center">
  <a href="https://docs.sayu-bot.com/" target="_blank">安装文档</a> &nbsp; · &nbsp;
  <a href="https://github.com/Genshin-bots/gsuid_core" target="_blank">gsuid_core</a>
</div>

## 丨安装提醒

> **注意：该插件为 [早柚核心(gsuid_core)](https://github.com/Genshin-bots/gsuid_core) 的扩展，具体安装方式可参考上方安装文档**
>
> 🚧 项目快速迭代中，有漏洞可提issue或pr 🚧

> [!CAUTION]
> 插件大部分功能依赖新版 gsuid_core ，请确保框架版本为最新版本！

## 丨支持平台

| 平台 | 图文 | 视频 | 音频 | 评论区 | 备注 |
| :---: | :---: | :---: | :---: | :---: | :---: |
| 哔哩哔哩 | ✅ | ✅ | 🚫 | ✅ | |
| 抖音 | ✅ | ✅ | 🚫 | ✅ | |
| 快手 | ✅ | ✅ | 🚫 | ✅ | |
| 微博 | ✅ | ✅ | 🚫 | ✅ | |
| 小红书 | ✅ | ✅ | 🚫 | ❌ | |
| X | ✅ | ✅ | 🚫 | ❌ | |
| Instagram | ✅ | ✅ | 🚫 | ❌ | |
| TikTok | ❌ | ✅ | 🚫 | ❌ | |
| YouTube | ❌ | ✅ | ✅ | ❌ | |
| 知乎 | ✅ | ✅ | 🚫 | ✅ | |
| 百度贴吧 | ✅ | ✅ | 🚫 | ✅ | |
| NGA | ✅ | 🚫 | 🚫 | ❌ | |
| 小黑盒 | ✅ | ✅ | 🚫 | ❌ | |
| JMComic | ✅ | 🚫 | 🚫 | ✅ | |
| Pixiv | ✅ | 🚫 | 🚫 | ❌ | |
| Iwara | ✅ | ✅ | 🚫 | ❌ | |
| 磁力链接 | ✅ | 🚫 | 🚫 | 🚫 | |
| 网易云音乐 | 🚫 | 🚫 | ✅ | ❌ | |
| QQ音乐 | 🚫 | 🚫 | ✅ | ❌ | |
| 酷狗音乐 | 🚫 | 🚫 | ✅ | ❌ | |
| 酷我音乐 | 🚫 | 🚫 | ✅ | ❌ | |
| 波点音乐 | 🚫 | 🚫 | ✅ | ❌ | |
| 没找到你想要的解析器？ |❓ |❓ |❓ | ❓| 参考[解析器添加和编写](#解析器添加和编写)一键适配对应平台！ |

## 丨添加其他解析器 / 皮肤包
#### 解析器添加和编写
> 编写可以使用本插件 [`parser-development`](.agents/skills/parser-development) 技能让 ai agent 适配对应网站
> 编写完毕后可以将第三方解析器包放到 `data\ParserUID\thirdparty\parsers` 文件夹中
> 插件会在启动时自动扫描此目录的解析器并注册
>
> **❤ 欢迎 PR 你适配的平台解析器到本项目中！❤**

#### 皮肤包添加和编写
> 编写可以使用本插件 [`parser-skin-development`](.agents\skills\parser-skin-development) 技能让 ai agent 进行编写
> 编写完毕后可以将第三方皮肤包放到 `data\ParserUID\thirdparty\skins`文件夹中
> 插件会在启动时自动扫描此目录的皮肤包并注册
>
> 插件提供为对应平台写专属皮肤的需求，仅需为对应平台准备html模板，然后在包中把对应模板命名为对应解析器相同内部名称，插件在触发卡片渲染时将优先调用专属平台卡片进行渲染
>
> **[注意]** 插件渲染依赖 **[pytakumi](https://github.com/KimigaiiWuyi/pytakumi)**，部分特效支持不稳定。因此请确保您写的 html 模板可以被 pytakumi 渲染！
>
> **❤ 欢迎 PR 你的皮肤包到本项目中！❤**
## 丨其他

- 本项目仅供学习使用，请勿用于商业用途
- [GPL-3.0 License](LICENSE)

## 丨QA
#### 1. 插件里没有我想要的解析器？
> 本插件内置了解析器开发 skill 以及第三方解析器解析能力，可以通过上方教程放入自己编写的解析器

#### 2. 觉得插件自带的渲染卡片皮肤不好看？
> 本插件内置了皮肤包开发 skill 以及第三方皮肤包代理渲染能力，可以通过上方教程放入自己编写的皮肤包

#### 3. 解析器发送了渲染卡片但是不发送视频 / 文件？
> 如为 docker 环境，请检查您的协议端是否**挂载框架 data 目录**以及挂载目录**是否对应正确**


## 致谢
| 贡献者 | 引用 |
|:------:|:------:|
| [Wuyi 无疑](https://github.com/KimigaiiWuyi) | 框架作者 |
| [gsuid_core](https://github.com/Genshin-bots/gsuid_core) | 插件依赖框架 |
| [karin-plugin-kkk](https://github.com/ikenxuan/karin-plugin-kkk) | 渲染卡片参考 |
| [KuGouMusicApi](https://github.com/MakcRe/KuGouMusicApi) | 酷狗音乐解析 |
| [astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser) | 移植插件上游项目 |
| [nonebot-plugin-parser-lite](https://github.com/sokoko-org/nonebot-plugin-parser-lite) | 大部分接口参考 |
| [astrbot_plugin_parser_plus](https://github.com/sssysy/astrbot_plugin_parser_plus) | 该项目移植自本人此 Astrbot 插件 |
| [NeteaseCloudMusicApiEnhanced](https://github.com/neteasecloudmusicapienhanced/api-enhanced) | 网易云解析 |
