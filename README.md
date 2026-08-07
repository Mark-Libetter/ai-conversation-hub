# AI Conversation Hub · Lite

> 把多个 AI 编程助手的对话只读汇聚成一条可搜索的时间线，帮你找回记忆、复盘工作。
> 本地运行、零第三方依赖、对原始数据只读。

## 这是什么

如果你同时用 Codex、Hermes、WorkBuddy、QoderWork、Claude Code、ZCode 等 AI 编程助手，对话散落在各家，找一个"之前在哪聊过这个"很痛苦。这个工具把它们汇聚到一个本地界面，让你：

- **跨源搜索**：在所有 agent 的对话里做布尔全文检索（AND/OR/NOT/短语/括号）
- **每日回顾**：自动生成当天的工作日报（规则版，离线可用，不调模型）
- **对话详情**：看完整对话内容，加收藏、标签、备注，导出 Markdown

## 界面预览

![演示动图](assets/demo.gif)

| 找对话 | 每日回顾 | 我的项目 |
|---|---|---|
| ![找对话](assets/find.png) | ![每日回顾](assets/daily.png) | ![我的项目](assets/projects.png) |

## 设计原则

| 原则 | 怎么做到 |
|---|---|
| **只读** | 对各 agent 的原始数据只读，绝不修改 |
| **本地优先** | 全程本机运行，服务只绑定 `127.0.0.1`，数据不经过云端 |
| **零依赖** | 纯 Python 标准库，无需 pip install |
| **离线可用** | 搜索和每日回顾全程本地，不需要模型 |

## 内置数据源

首版内置 7 个适配器：

| Agent | 默认发现位置 |
|---|---|
| **Hermes** | `~/.hermes/state.db`（可用环境变量 `CONVERSATION_HUB_HERMES_DB` 覆盖） |
| **Codex** | `~/.codex/state_5.sqlite` + rollout JSONL（尊重 `CODEX_HOME`） |
| **WorkBuddy** | `~/.workbuddy/`（尊重 `WORKBUDDY_HOME`） |
| **Claude Code** | `~/.claude/` |
| **QoderWork** | `%APPDATA%/QoderWork CN/data/agents.db`（兼容改名后的 `QoderWork` / `QwenWorkCN` / `QwenWork` 目录，新旧数据自动合并） |
| **ZCode** | `~/.zcode/cli/db/db.sqlite` |
| **ChatGPT** | 网页端导出的 `conversations.json`（官方数据包或插件导出；放 ~/Downloads 自动发现，或在设置里指定路径） |

**网页端其它助手（千问 / Gemini / Claude 网页版等）**：用浏览器导出插件或油猴脚本把对话导出为
Markdown / JSON，再作为自定义源接入即可，无需改代码。可参考
[chatgpt-exporter](https://github.com/pionxzh/chatgpt-exporter)、
[AI-Chat-Md-Export](https://github.com/YunAsimov/AI-Chat-Md-Export)。

**想接入其它 agent？** 支持 JSONL / Markdown / SQLite 三种自定义格式，无需改代码，见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 快速开始

### 环境要求
- Python 3.10+（仅标准库）
- Windows / macOS

### 运行
```bash
python server.py        # Windows
python3 server.py       # macOS / Linux
```
浏览器打开 `http://127.0.0.1:8765`。

首次运行会自动发现本机的数据源。如果自动发现失败，在「设置 → 数据源质量中心 → 配置路径」里手动指定。

### 桌面启动（Windows）
双击 `launcher.py` 或运行 `python launcher.py`，会自动启动服务并打开浏览器。
`修复数据源.cmd` 是 Windows 下的数据源修复快捷入口。

### 桌面启动（macOS）
双击 `start-macos.command`（首次可能需要在「安全性与隐私」里允许打开），它会自动寻找
python3、启动服务并打开浏览器；或手动运行 `python3 launcher.py`。
macOS 的数据目录约定与 Windows 一致：各 Agent 的 `~/.codex`、`~/.hermes`、`~/.workbuddy`、
`~/.claude`、`~/.zcode`，以及 `~/Library/Application Support/` 下的 QoderWork/千问办公 数据库。

## 功能一览

### 找对话
- 跨 6 个源的布尔全文检索
- 智能搜索：自然语言自动转布尔检索式
- 筛选：时间范围、状态、工作区、只看收藏
- 对话详情：可追溯概览、收藏、标签、备注、导出 Markdown
- 支持勾选多个对话批量导出

### 每日回顾
- **今日要点**：平等列出当天各事项，带数据来源标签，点击展开看最近对话原文，一键跳转到该对话
- **完整日报**：概览、已完成、关键决定、待继续、受阻、下一步
- 日期切换：查看任意一天，支持日历选择
- 按消息时间戳归入自然日（Asia/Shanghai），跨天的长对话会正确拆分到各自日期
- 规则版离线可用，不依赖模型

## 配置

不要把你的真实 `sources.json` 提交到仓库。首次运行会自动生成；也可以参考
[sources.example.json](sources.example.json) 手动创建。支持的环境变量：

```text
CONVERSATION_HUB_DATA_DIR=<Hub 数据目录>
CONVERSATION_HUB_HERMES_DB=<state.db 路径>
HERMES_HOME=<包含 state.db 的 Hermes 目录>
CONVERSATION_HUB_CODEX_DB=<state_5.sqlite 路径>
CODEX_HOME=<Codex 主目录>
WORKBUDDY_HOME=<包含 workbuddy.db 与 projects 的目录>
```

## 隐私与安全

- 原始对话数据**只读**，工具绝不写回 agent 的数据库
- 只索引用户与助手的正文；系统提示、推理、工具调用、子任务与常见密钥模式会被过滤
- 你的收藏/备注/标签存在独立的 `hub_notes.sqlite`，与原始数据分开
- 服务只绑定 `127.0.0.1`，搜索与日报全程本地，不发送任何数据到云端
- 内置的"安全更新"仅在你手动填写清单地址后才会联网，下载校验 SHA-256，绝不自动执行
- 详见 [PRIVACY.md](PRIVACY.md) 与 [DESIGN_AND_SAFETY.md](DESIGN_AND_SAFETY.md)

## 项目结构

```
server.py           # 后端：HTTP 服务 + 索引 + 搜索 + 每日回顾
source_adapters.py  # 数据源适配器（内置 6 个 + 自定义源框架）
static/
  app.js            # 前端逻辑
  index.html        # 页面结构
  app.css           # 样式
launcher.py         # 跨平台桌面启动器（起服务+开浏览器）
desktop_app.py      # 桌面应用壳
app_paths.py        # 数据/资源目录解析（含 macOS 路径约定）
repair_sources.py   # 数据源配置修复工具（修复数据源.cmd 是它的 Windows 快捷入口）
start-macos.command # macOS 双击启动脚本
sources.example.json# 数据源配置示例
```

## 许可证

本项目采用 **MIT** 许可证。

- ✅ 你可以自由使用、修改、分享、商用
- ✅ 必须保留原作者的版权声明与许可声明
- 软件按"现状"提供，不提供任何担保

详见 [LICENSE](LICENSE)。

## 致谢

本项目源自个人 AI 编程实践，感谢所有被接入的 AI 编程助手的设计者。
