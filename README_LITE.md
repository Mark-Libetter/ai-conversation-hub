# AI Conversation Hub · Lite

> 把多个 AI 编程助手的对话只读汇聚成一条可搜索的时间线，帮你找回记忆、复盘工作。
> 本地运行、零第三方依赖、对原始数据只读。

## 这是什么

如果你同时用 Codex、Hermes、WorkBuddy、QoderWork、Claude Code 等 AI 编程助手，对话散落在各家，找一个"之前在哪聊过这个"很痛苦。这个工具把它们汇聚到一个本地界面，让你：

- **跨源搜索**：在所有 agent 的对话里做布尔全文检索（AND/OR/NOT/短语/括号）
- **每日回顾**：自动生成当天的工作日报（规则版，离线可用，不调模型）
- **对话详情**：看完整对话内容，加收藏、标签、备注，导出 Markdown

## 设计原则

| 原则 | 怎么做到 |
|---|---|
| **只读** | 对各 agent 的原始数据只读，绝不修改 |
| **本地优先** | 全程本机运行，数据不经过云端 |
| **零依赖** | 纯 Python 标准库，无需 pip install |
| **离线可用** | 搜索和每日回顾全程本地，不需要模型 |

## 内置数据源

首版内置 5 个适配器：

| Agent | 数据位置 |
|---|---|
| **Hermes** | `~/.codex/` 同级 Hermes 数据库 |
| **Codex** | `~/.codex/state_5.sqlite` + rollout JSONL |
| **WorkBuddy** | `~/.workbuddy/` |
| **QoderWork** | `%APPDATA%/QoderWork CN/data/agents.db` |
| **Claude Code** | `~/.claude/` |

**想接入其它 agent？** 见 [CONTRIBUTING.md](CONTRIBUTING.md)，支持 JSONL / Markdown / SQLite 三种自定义格式，无需改代码。

## 快速开始

### 环境要求
- Python 3.10+（仅标准库）
- Windows / macOS

### 运行
```bash
python server.py
```
浏览器打开 `http://127.0.0.1:8765`。

首次运行会自动发现本机的数据源。如果自动发现失败，在「设置」里手动配置路径。

### 桌面启动（Windows）
双击 `launcher.py` 或运行 `python launcher.py`，会自动启动服务并打开浏览器。

## 功能一览

### 找对话
- 跨 5 个源的布尔全文检索
- 智能搜索：自然语言自动转布尔检索式
- 筛选：时间范围、状态、工作区、只看收藏
- 对话详情：可追溯概览、收藏、标签、备注、导出 Markdown

### 每日回顾
- **今日要点**：平等列出当天各事项，带数据来源标签，点击展开看最近对话原文，一键跳转到该对话
- **完整日报**：概览、已完成、关键决定、待继续、受阻、下一步
- 日期切换：查看任意一天，支持日历选择
- 规则版离线可用，不依赖模型

## 隐私与安全

- 原始对话数据**只读**，工具绝不写回 agent 的数据库
- 你的收藏/备注/标签存在独立的 `hub_notes.sqlite`，与原始数据分开
- 全程本地，不发送任何数据到云端
- 详见 [PRIVACY.md](PRIVACY.md)

## 项目结构

```
server.py           # 后端：HTTP 服务 + 索引 + 搜索 + 每日回顾
source_adapters.py  # 数据源适配器（内置5个 + 自定义源框架）
static/
  app.js            # 前端逻辑
  index.html        # 页面结构
  app.css           # 样式
launcher.py         # Windows 桌面启动器
desktop_app.py      # 桌面应用壳
```

## 许可证

本项目采用 **CC BY-NC 4.0**（署名-非商业性使用 4.0）许可证。

- ✅ 你可以自由使用、修改、分享、学习
- ✅ 必须保留原作者署名
- ❌ **不得用于商业用途**；如需商用，请联系作者另行授权

详见 [LICENSE](LICENSE)。

## 致谢

本项目源自个人 AI 编程实践，感谢所有被接入的 AI 编程助手的设计者。
