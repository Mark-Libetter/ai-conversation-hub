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

首版内置 6 个适配器：

| Agent | 默认发现位置 |
|---|---|
| **Hermes** | `~/.hermes/state.db`（可用环境变量 `CONVERSATION_HUB_HERMES_DB` 覆盖） |
| **Codex** | `~/.codex/state_5.sqlite` + rollout JSONL（尊重 `CODEX_HOME`） |
| **WorkBuddy** | `~/.workbuddy/`（尊重 `WORKBUDDY_HOME`） |
| **Claude Code** | `~/.claude/` |
| **QoderWork** | `%APPDATA%/QoderWork CN/data/agents.db`（兼容改名后的 `QoderWork` / `QwenWorkCN` / `QwenWork` 目录，新旧数据自动合并） |
| **ZCode** | `~/.zcode/cli/db/db.sqlite` |

**想接入其它 agent？** 支持 JSONL / Markdown / SQLite 三种自定义格式，无需改代码，见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 未来方向

- **网页端对话接入**（保留方向，暂未内置）：ChatGPT / 千问 / Gemini / Claude 网页版等的
  聊天记录存在各家服务器，本地无可靠数据源。计划支持读取浏览器导出的
  `conversations.json` / Markdown / JSON（参考
  [chatgpt-exporter](https://github.com/pionxzh/chatgpt-exporter)、
  [AI-Chat-Md-Export](https://github.com/YunAsimov/AI-Chat-Md-Export) 等项目的格式）。
  现阶段可先用自定义源（Markdown/JSON）手动接入。

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

### 初次使用指南

**第 1 步：启动**
- Windows：双击 `launcher.py`（或 `修复数据源.cmd`），自动启动服务并打开浏览器
- macOS：双击 `start-macos.command`
- 命令行：`python server.py`，然后浏览器打开 `http://127.0.0.1:8765`

> 首次启动会自动扫描本机已安装的 AI 编程助手（Codex / Hermes / WorkBuddy / Claude Code / QoderWork / ZCode），通常无需手动配置。

**第 2 步：确认数据源**（如果左侧栏没显示对话）
1. 点击左侧栏底部「设置」⚙
2. 找到「数据源质量中心」-> 点「配置路径」
3. 检查各 Agent 的路径是否正确（绿色 = 正常，红色 = 路径不对）
4. 路径不对的，手动粘贴正确路径 -> 点「验证并开始使用」

![配置路径](assets/guide-setup.png)

**第 3 步：搜索对话**
1. 在顶部搜索框输入关键词（如 `API 修复`），直接回车
2. 支持布尔语法：`API OR 接口`、`修复 NOT 测试`、`"精确短语"`、`(A OR B) C`
3. 智能搜索（默认开启）：直接说人话，如"昨天关于抖音的对话"，自动转检索式
4. 左侧可按 Agent 筛选，顶部可按时间范围筛选

![搜索对话](assets/guide-search.png)

**第 4 步：查看对话详情**
- 点击列表中任一对话，右侧展开完整内容
- 可添加收藏 ★、标签、备注、状态（待继续/已完成）
- 顶部搜索框可在当前对话内搜索关键词

![对话详情](assets/guide-detail.png)

**第 5 步：每日回顾**
1. 点击左侧「每日回顾」
2. 查看今天的「今日要点」：各事项带来源标签，点击展开看最近对话原文
3. 点 ‹ › 切换日期，或点中间日期打开日历选任意一天
4. 展开「完整日报」看：概览、已完成、关键决定、待继续、下一步

![每日回顾](assets/guide-daily.png)

**第 6 步：组织项目**
1. 在「找对话」里勾选相关对话（点对话左侧方框）
2. 点选择栏的「归入项目」-> 选已有项目或新建
3. 在「我的项目」里查看项目详情：
   - **状态标签**：进行中/已完成/暂停，点击切换
   - **项目笔记**：记录关键结论和决策
   - **任务清单**：添加待办，勾选完成
   - **对话标注**：给每条对话写一句话备注（为什么重要）

![我的项目](assets/guide-projects.png)

**第 7 步：导出对话**
1. 在「找对话」勾选要导出的对话
2. 点选择栏的「导出所选」（或到「工具」页选"已勾选的对话"）
3. 选 Markdown 或 JSONL 格式 -> 生成预览 -> 下载
4. 导出的 Markdown 每个对话带来源 Agent、标题、对话 ID，清晰可区分

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

### 我的项目
- 把相关对话归到一起，集中回顾和导出
- 项目状态：进行中/已完成/暂停
- 项目笔记：记录关键结论和决策
- 任务清单：轻量待办，勾选完成
- 对话标注：给每条对话写项目级备注

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
- 内置的"检查更新"直接链接到 GitHub Releases 页面，下载最新版本解压即用
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
