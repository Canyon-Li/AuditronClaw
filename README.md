<div align="center">

![AuditronClaw Logo](docs/cyber_logo.png)

# AuditronClaw

###  **当 AI 开始"黑箱操作"，你需要一双透视眼**

[![AuditronClaw](https://img.shields.io/badge/AuditronClaw-1.0.0-purple.svg?logo=cyberpunk)](https://github.com/Canyon-Li/AuditronClaw)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.x-blue.svg)](https://langchain-ai.github.io/langgraph/)
[![LangChain](https://img.shields.io/badge/LangChain-1.x-blue.svg)](https://python.langchain.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-Passing-brightgreen.svg)](tests/)
[![GitHub](https://img.shields.io/badge/GitHub-@Canyon-Li-black.svg?logo=github)](https://github.com/Canyon-Li)

**下一代透明智能体架构** · Next-Gen Transparent Agent Architecture

🌐 Language: [中文](#中文) · [English](#english)

中文导航: [快速开始](#-快速开始) · [核心能力](#-核心能力) · [架构图](#-系统架构) · [示例](#-基本用法)

English Nav: [Quick Start](#-quick-start) · [Core Capabilities](#-core-capabilities) · [Architecture](#-system-architecture) · [Examples](#-basic-usage)

</div>

---

> 🤖 **你的 AI 在背着你做什么？AuditronClaw 让所有行为无所遁形**
> 
> 💡 **灵感来源**：受 [OpenClaw](https://github.com/openclaw/openclaw) 的启发，AuditronClaw 专注于解决 AI 智能体的透明度和可控性问题。
>
> 🙏 **致谢**：本项目 fork 自 [CyberClaw](https://github.com/ttguy0707/CyberClaw)（原作者 @ttguy0707），基于 MIT 许可证进行二次开发。原始版权见 [LICENSE](LICENSE)。

---

<a id="中文"></a>


## 📖 简介

AuditronClaw 是一个**企业级透明可控智能体**，重新定义 AI 系统的可信边界：

- **🔍 白盒化决策** → 5 类事件审计 + JSONL 日志 + Rich 监控终端，所有行为可追溯
- **🛡️ 零信任执行** → 两段式调用（help → run），先看说明书再执行，P0 级事故率降低 80%
- **🧠 持续学习** → 双水位记忆系统（长期画像 + 短期摘要），越用越懂你
- **⚡ 复杂任务编排** → 心跳任务系统 + 可插拔技能 + MCP 服务集成，解放双手

### 🔌 技能生态兼容

AuditronClaw 支持**OpenClaw 技能**和**Claude Code 技能**，可直接使用两个生态系统的丰富技能资源，无需重新开发。

### 🌟 核心能力

| 能力 | 说明 | 优势 |
|------|------|------|
| **🧠 双水位记忆** | 长期画像 + 短期摘要，持续学习用户偏好 | 越用越懂你，避免重复询问 |
| **🔍 全行为审计** | 5 类事件实时审计，JSONL 日志 + Rich 监控终端 | 告别黑箱，所有决策可追溯 |
| **🛡️ 零信任执行** | help → run 两段式调用，先看说明书再执行 | P0 级事故率降低 80%（50% → 10%）|
| **⏰ 心跳任务引擎** | 后台独立进程，自动执行定时任务 | 解放双手，复杂任务自动化 |
| **🖥️ 跨平台支持** | Unix + Windows 双平台自适应，LLM 自主选择命令 | 一套代码，全平台运行 |

---

## ✨ 功能特性

### 🧠 智能核心

- **双水位记忆系统**
  - 长期画像 (`user_profile.md`)：用户偏好、职业、特殊要求
  - 近期摘要 (SQLite)：每 MAX_TURNS 轮自动摘要，保留最近 KEEP_TURNS 轮
  - 上下文修剪：智能保留关键对话，防止 Token 爆炸

- **两段式技能调用**
  - `mode='help'`：查看完整说明书（SKILL.md）
  - `mode='run'`：执行具体操作
  - 支持反悔机制：看完说明书可以换工具

- **透明监控系统**
  - 5 类事件审计：`llm_input`, `tool_call`, `tool_result`, `ai_message`, `system_action`
  - JSONL 日志格式，支持 `tail -f` 实时监控
  - Rich 终端 UI，颜色/面板区分事件类型

- **心跳任务系统**
  - 后台独立进程，每秒检查任务队列
  - 支持 daily/weekly/monthly 循环任务
  - 任务持久化存储，重启不丢失

### 🛡️ 安全沙盒

- **跨平台路径拦截**
  - Unix + Windows 双平台越权拦截
  - 禁止 `..`、绝对路径、用户主目录访问
  - 所有操作限制在 `office/` 工位内

- **Shell 命令安全**
  - 危险命令正则匹配拦截
  - 60 秒超时熔断
  - 非交互式执行（必须带 `-y` 等参数）

### 🖥️ 跨平台特性

- **系统信息注入** - 自动识别操作系统，注入平台相关信息
- **LLM 自主选择命令** - 根据平台特性生成合适的命令（PowerShell / Bash）
- **路径格式兼容** - 自动处理 `/` 和 `\` 路径分隔符
- **环境变量适配** - 跨平台环境变量读取和设置

### 🔧 内置工具

| 工具 | 功能 | 示例 |
|------|------|------|
| `get_current_time` | 获取当前时间 | "现在几点了？" |
| `calculator` | 数学计算器 | "25 乘以 48 等于多少" |
| `schedule_task` | 定时任务/闹钟 | "每天早上 8 点提醒我喝水" |
| `list_scheduled_tasks` | 查看任务列表 | "我都有哪些任务" |
| `delete_scheduled_task` | 删除任务 | "取消明天的会议提醒" |
| `modify_scheduled_task` | 修改任务 | "把 8 点的会议改成 9 点" |
| `get_system_model_info` | 获取模型信息 | "你是什么模型" |
| `save_user_profile` | 更新用户画像 | "记住我喜欢喝冰美式" |
| `list_office_files` | 列出文件 | "看看 office 里有什么" |
| `read_office_file` | 读取文件 | "读取 readme.txt" |
| `write_office_file` | 写入文件 | "创建 test.py" |
| `execute_office_shell` | 执行 Shell 命令 | "运行 python test.py" |

### 🎯 可插拔技能

- **动态加载**：自动扫描 `workspace/office/skills/` 目录
- **SKILL.md 规范**：每个技能包含完整说明书
- **兼容 OpenClaw 和 Claude Code 技能**：可直接使用两个生态系统的技能
- **推荐技能**：
  - `skill-creator`：用自然语言让 AuditronClaw 自己创建技能
  - `skill-vetter`：检查技能的安全性
  - `mcporter`：连接外部 MCP (Model Context Protocol) 服务
  - `mcp-builder`：构建自己的 MCP 服务
  - `tavily-search`：AI 优化网络搜索
  - `weather`：天气查询

---

## 🚀 快速开始

### 1️⃣ 安装

```bash
# 克隆项目
git clone https://github.com/Canyon-Li/AuditronClaw.git
cd AuditronClaw

# 安装依赖并注册命令行工具（一步完成）
pip install -e .
```

> 💡 **推荐使用虚拟环境**：
> ```bash
> # 创建虚拟环境
> python3 -m venv venv
> source venv/bin/activate  # Windows: venv\Scripts\activate
> 
> # 安装项目（会自动安装 requirements.txt 中的依赖）
> pip install -e .
> ```
> 
> 安装完成后，即可在任意目录使用 `auditronclaw` 命令。

### 2️⃣ 配置

有两种配置方式：**自动配置向导**（推荐）或 **手动配置**。

#### 方式一：自动配置向导（推荐）

```bash
# 启动交互式配置向导
auditronclaw config
```

配置向导会引导你：
1. 选择模型提供商（OpenAI / Anthropic / 阿里云 / 腾讯 / Z.AI / Ollama）
2. 输入 API Key
3. 配置 Base URL（可选）
4. **自动测试连接**，确保配置正确

![配置向导](docs/config.png)

#### 方式二：手动配置

```bash
# 复制示例配置文件
cp .env.example .env

# 编辑配置文件
vim .env  # 或使用你喜欢的编辑器
```

编辑 `.env` 文件，配置必要的参数：

```bash
# 模型提供商
DEFAULT_PROVIDER=aliyun
DEFAULT_MODEL=glm-5

# API Key (根据提供商选择对应的 Key)
OPENAI_API_KEY=sk-your-api-key-here

# Base URL (可选，使用代理时配置)
OPENAI_API_BASE=https://coding.dashscope.aliyuncs.com/v1
```

**配置说明：**
- `DEFAULT_PROVIDER`: 模型提供商 (`openai`, `anthropic`, `aliyun`, `tencent`, `z.ai`, `ollama`)
- `DEFAULT_MODEL`: 模型名称 (如 `gpt-4o-mini`, `glm-5`, `qwen-max`)
- `OPENAI_API_KEY`: OpenAI 或兼容接口的 API Key
- `ANTHROPIC_API_KEY`: Anthropic 的 API Key
- `OPENAI_API_BASE`: 兼容接口的 Base URL（阿里云、腾讯云等）
- `OLLAMA_BASE_URL`: Ollama 本地服务地址（默认 `http://localhost:11434`）

> 💡 **工作区配置**：工作区路径已在代码中初始化，默认为项目根目录的 `workspace` 文件夹，无需在 `.env` 中配置。仅当需要自定义工作区位置时，才设置 `AUDITRONCLAW_WORKSPACE` 环境变量。

> 💡 提示：配置完成后，可运行 `auditronclaw run` 聊天测试连接是否正常。

### 3️⃣ 运行

```bash
# 启动主程序
auditronclaw run
```

![欢迎界面](docs/welcome.png)

### 4️⃣ 基本用法

启动后进入交互式对话界面，如图所示：

![聊天界面](docs/chat.png)

**常用命令示例：**

| 类型 | 命令示例 | 说明 |
|------|----------|------|
| ⏰ 时间查询 | `现在几点了？` | 获取当前时间 |
| 🧮 数学计算 | `帮我算一下 25 乘以 48` | 调用计算器工具 |
| ⏲️ 定时任务 | `每天早上 8 点提醒我喝水` | 创建循环任务 |
| 📋 查看任务 | `我都有哪些任务` | 查看任务列表 |
| ✏️ 修改任务 | `把 8 点的喝水提醒改成 9 点` | 修改已有任务 |
| ❌ 删除任务 | `取消明天的会议提醒` | 删除任务 |
| 📁 文件操作 | `看看 office 里有什么文件` | 列出工位文件 |
| 📖 读取文件 | `读取 readme.txt` | 读取文件内容 |
| 📝 创建文件 | `创建 test.py` | 写入新文件 |
| 💻 Shell 命令 | `运行 python test.py` | 执行 Shell 命令 |
| 🚪 退出 | `/exit` | 退出程序 |

### ⏰ 心跳任务系统

AuditronClaw 内置心跳任务系统（Heartbeat），自动在后台执行定时任务：

- **自动触发**：心跳进程每秒检查任务队列，到点自动触发
- **循环任务**：支持 daily/weekly/monthly 循环模式
- **任务持久化**：任务保存在 `workspace/tasks.json`，重启不丢失
- **实时监控**：运行 `auditronclaw monitor` 可查看任务执行日志

**心跳任务示例：**
```bash
# 创建循环任务
> 每天早上 8 点提醒我喝水
✅ 任务已加入队列 | 循环模式：daily | 首发时间：2026-04-07 08:00:00

# 心跳系统会在每天 8:00 自动触发提醒
```

> 💡 提示：心跳任务在后台运行，即使不启动主程序也会执行（需单独运行心跳进程）。

### 5️⃣ 监控终端

在另一个终端运行：
```bash
auditronclaw monitor
```

![监控终端](docs/monitor.png)

---

## 🏢 适用场景

### 🔒 企业级应用
- **合规审计** - 5 类事件审计日志，满足企业合规要求
- **权限管控** - 沙盒隔离 + 路径拦截，防止越权操作
- **任务自动化** - 心跳任务引擎，定时执行重复性工作
- **知识沉淀** - 双水位记忆系统，持续学习组织偏好

### 🧪 AI 研究与开发
- **Agent 行为分析** - 完整记录 LLM 决策过程和工具调用链
- **安全研究** - 两段式调用机制，研究 AI 安全边界
- **调试友好** - JSONL 日志 + Rich 监控终端，快速定位问题
- **可扩展架构** - 可插拔技能系统，快速验证新想法

### 🖥️ 跨平台部署
- **Windows** - 完整支持 PowerShell + CMD，路径自动适配
- **Linux** - 原生支持所有发行版，完美兼容 Bash
- **macOS** - 支持 zsh/bash，与 Unix 工具链无缝集成

### 🛠️ 开发者工具
- **本地开发助手** - 文件操作 + Shell 执行，自动化编码任务
- **项目监控** - 实时监控 AI 行为，防止意外操作
- **技能开发** - 支持自定义技能，快速集成新工具
- **MCP 服务集成** - 连接外部 MCP 服务，扩展能力边界

### 📚 教育与学习
- **AI 智能体教学** - 透明展示 Agent 架构和决策流程
- **Prompt 工程** - 观察不同 Prompt 对 AI 行为的影响
- **安全实践** - 学习 AI 安全最佳实践和防护措施
- **开源贡献** - 参与开源项目，积累实战经验

### 🏠 个人效率工具
- **智能日程管理** - 定时提醒 + 循环任务，解放双手
- **文件自动化** - 批量处理文件，自动化工作流
- **信息查询** - 集成搜索技能，快速获取信息
- **个性化助手** - 记忆系统学习个人偏好，越用越顺手

---

## 🏗️ 系统架构

### 完整架构图

![系统架构图](docs/architect.png)

**架构说明**：

- **输入层** (蓝色)：Heartbeat 心跳任务 + 用户输入 → Gateway 网关
- **记忆层** (粉色)：上下文裁剪 + 长短期记忆管理
- **智能决策层** (黄色)：Agent Loop + LLM 推理决策
- **工具执行层** (紫色)：内置工具集 + 可插拔 Skills
- **安全层** (橙色)：路径越权拦截 + 跨平台兼容
- **透明监控层** (绿色)：记忆更新 + 工具决策 + 工具参数 + 调用结果
- **输出层** (底部)：聊天终端 + 监控终端

### 核心模块

| 模块 | 文件 | 功能 |
|------|------|------|
| **Agent 循环** | `auditronclaw/core/agent.py` | LangGraph StateGraph，决策大脑 |
| **技能加载** | `auditronclaw/core/skill_loader.py` | 动态加载 SKILL.md，两段式调用 |
| **上下文管理** | `auditronclaw/core/context.py` | 消息修剪，双水位记忆 |
| **内置工具** | `auditronclaw/core/tools/builtins.py` | 时间/计算/任务调度等 |
| **沙盒工具** | `auditronclaw/core/tools/sandbox_tools.py` | 文件操作 + Shell 执行 |
| **审计日志** | `auditronclaw/core/logger.py` | JSONL 格式事件记录 |
| **心跳任务** | `auditronclaw/core/heartbeat.py` | 定时任务检查与触发 |

### 项目结构

```
AuditronClaw/
├── auditronclaw/                    # 核心包
│   ├── core/
│   │   ├── agent.py              # Agent 循环
│   │   ├── config.py             # 配置管理
│   │   ├── context.py            # 上下文修剪
│   │   ├── provider.py           # LLM 提供商适配
│   │   ├── skill_loader.py       # 动态技能加载
│   │   ├── logger.py             # 审计日志
│   │   ├── heartbeat.py          # 心跳任务
│   │   └── tools/
│   │       ├── base.py           # 工具装饰器
│   │       ├── builtins.py       # 内置工具
│   │       └── sandbox_tools.py  # 沙盒工具
│   └── __init__.py
├── workspace/
│   ├── office/                   # 沙盒工位
│   │   ├── skills/               # 可插拔技能
│   │   │   ├── weather/
│   │   │   ├── skill-creator/
│   │   │   └── ...
│   │   └── .env                  # 环境变量
│   ├── memory/
│   │   └── user_profile.md       # 用户长期画像
│   ├── state.sqlite3             # 对话历史数据库
│   └── tasks.json                # 定时任务队列
├── logs/
│   └── local_geek_master.jsonl   # 审计日志
├── docs/                         # 文档与架构图
│   ├── architect.png             # 系统架构图
│   ├── monitor.png               # 监控终端截图
│   ├── welcome.png               # 欢迎界面
│   ├── chat.png                  # 聊天界面
│   ├── config.png                # 配置向导
│   ├── memory.png                # 记忆系统
│   └── context_cut.png           # 上下文裁剪
├── entry/
│   ├── main.py                   # 主程序入口
│   ├── cli.py                    # CLI 配置向导
│   └── monitor.py                # 监控终端
├── tests/                        # 测试套件
│   ├── test_agent.py
│   ├── test_builtins.py
│   ├── test_two_phase_skills.py  # 两阶段测试
│   └── logs/                     # 测试报告
├── setup.py
├── .env                          # 环境配置（运行时创建）
├── .env.example                  # 环境配置示例（复制此文件开始配置）
└── README.md
```

---

## 📖 使用指南

### 配置文件说明

**`.env` 文件**：主配置文件，包含 API Key、模型设置等敏感信息。

**`.env.example` 文件**：配置模板，包含所有可用配置项的说明和示例值。

首次使用时，复制示例文件并修改：
```bash
cp .env.example .env
```

详细配置说明见 [快速开始 - 配置](#-配置) 部分。

### 技能系统
#### 安装技能

**方法 1：直接复制**
```bash
cp -r /path/to/skill workspace/office/skills/
```

**方法 2：使用 skill-creator**
```bash
# 先安装 skill-creator 技能
cd workspace/office/skills
git clone https://github.com/.../skill-creator.git

# 然后用自然语言让 AuditronClaw 创建新技能
> 帮我创建一个查询比特币价格的技能
```

**方法 3：使用 skill-vetter 检查安全性**
```bash
# 安装 skill-vetter
cd workspace/office/skills
git clone https://github.com/.../skill-vetter.git

# 让 AuditronClaw 检查技能安全性
> 帮我检查一下 weather 技能是否安全
```

#### 技能规范

每个技能包含 `SKILL.md`：

````markdown
---
name: weather
description: 获取天气预报
---

# Weather Skill

## 功能
获取全球城市的实时天气预报。

## 命令示例
```bash
curl "wttr.in/Beijing?format=3"
```

## 参数
- 城市名（必填）
- 天数（可选）
````

### 定时任务

```bash
# 单次任务
> 明天早上 9 点叫我起床

# 循环任务
> 每天早上 8 点提醒我喝水
> 每周一上午 10 点开团队会议

# 查看任务
> 我都有哪些任务

# 修改任务
> 把 8 点的喝水提醒改成 9 点

# 删除任务
> 取消明天的会议提醒
```

### 高级用法

#### 1. 使用监控器

在另一个终端运行：
```bash
auditronclaw monitor
```

实时查看：
- 🧠 LLM 输入
- 💡 工具调用
- 💻 工具结果
- 🤖 AI 回复
- ⚙️ 系统动作

#### 2. 查看审计日志

```bash
# 实时监控
tail -f logs/local_geek_master.jsonl

# 搜索特定事件
grep "tool_call" logs/local_geek_master.jsonl | tail -20
```

#### 3. 自定义用户画像

编辑 `workspace/memory/user_profile.md`：

```markdown
# 用户档案

- **姓名**: Thor Allen
- **职业**: 程序员
- **偏好**: 
  - 喜欢喝冰美式咖啡
  - 常用 Python 写代码
  - 每天 8 点起床
- **特殊要求**:
  - 回答要简洁
  - 不要使用表情符号
```

---

## 🧠 记忆系统

### 双水位记忆架构

![记忆系统](docs/memory.png)

- **长期记忆**：`user_profile.md` Markdown 文件，存储用户偏好、职业、特殊要求
- **短期记忆**：SQLite 数据库，存储完整对话历史
- **自动摘要**：每 20 轮对话自动触发摘要，保留最近 10 轮

### 上下文裁剪

![上下文裁剪](docs/context_cut.png)

当对话轮次超过阈值时：
1. 系统消息始终保留
2. 保留最近 N 轮完整对话
3. 旧对话压缩为摘要
4. 防止 Token 爆炸

### 轮次记忆

![轮次记忆](docs/turn_memory.png)

每个完整回合包含：
- 用户消息 (HumanMessage)
- AI 回复 (AIMessage)
- 工具调用 (ToolMessage)

---

## 🧪 测试

### 运行测试

```bash
# 运行所有测试
python3 -m pytest tests/ -v

# 运行特定测试
python3 tests/test_two_phase_skills.py

# 运行两阶段测试
python3 -c "from tests.test_two_phase_skills import run_tests; run_tests()"
```

### 测试覆盖

| 测试文件 | 测试内容 | 状态 |
|---------|---------|------|
| `test_agent.py` | Agent 循环 | ✅ 通过 |
| `test_builtins.py` | 内置工具 | ✅ 通过 |
| `test_context.py` | 上下文修剪 | ✅ 通过 |
| `test_sandbox_tools.py` | 沙盒工具 | ✅ 通过 |
| `test_two_phase_skills.py` | 两阶段调用 | ✅ 通过 |
| `test_heartbeat.py` | 心跳任务 | ✅ 通过 |

### 两阶段测试报告

根据 `tests/logs/test_two_phase_skills.md` 的实验数据：

| 指标 | 单阶段 | 两阶段 | 提升 |
|------|--------|--------|------|
| **安全命中率** | 50.0% | 90.0% | **+40%** |
| **P0 级事故率** | 50.0% | 10.0% | **-80%** |
| **平均决策耗时** | 19.33s | 23.88s | +23.5% |

**结论**：两阶段架构用 23.5% 的时间开销，换来了**事故率从 50% 暴降至 0%**（实际破坏性执行为 0）。

---

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

### 开发环境

```bash
# 克隆项目
git clone https://github.com/Canyon-Li/AuditronClaw.git
cd AuditronClaw

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装开发依赖
pip install -e ".[dev]"
```

### 提交规范

- `feat:` 新功能
- `fix:` 修复 bug
- `docs:` 文档更新
- `style:` 代码格式
- `refactor:` 重构
- `test:` 测试相关
- `chore:` 构建/工具

---

## 📄 许可证

MIT License

---

## 🙏 致谢

- **[OpenClaw](https://github.com/openclaw/openclaw)** - 灵感来源与技能生态
- **LangChain** - LLM 应用开发框架
- **LangGraph** - 有状态 Agent 构建
- **Rich** - 终端美化
- **Prompt Toolkit** - 交互式命令行
- **所有贡献者** - 感谢你们的贡献！

---

## 📬 联系方式

- **GitHub**: [@Canyon-Li](https://github.com/Canyon-Li)
- **邮箱**: allen.wtyummy@gmail.com

---

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Canyon-Li/AuditronClaw&type=Date)](https://star-history.com/#Canyon-Li/AuditronClaw&Date)

---

<div align="center">

**👾 AuditronClaw · 下一代透明智能体架构**

Made with ❤️ by [@Canyon-Li](https://github.com/Canyon-Li)

</div>

---

<a id="english"></a>


> 🤖 **What is your AI doing behind the scenes? AuditronClaw makes every action visible.**
>
> 💡 **Inspired by** [OpenClaw](https://github.com/openclaw/openclaw), AuditronClaw focuses on transparency and controllability for AI agents.

---

## 📖 Introduction

AuditronClaw is an **enterprise-grade transparent and controllable agent** that redefines the trust boundary of AI systems:

- **🔍 White-box decisions** -> 5-category event auditing, JSONL logs, and a Rich monitoring terminal make every action traceable
- **🛡️ Zero-trust execution** -> two-phase invocation (`help` -> `run`) lets the agent read instructions before execution, reducing P0 incident risk by 80%
- **🧠 Continuous learning** -> dual-watermark memory, combining a long-term profile with short-term summaries, learns your preferences over time
- **⚡ Complex task orchestration** -> heartbeat tasks, pluggable skills, and MCP service integration automate repetitive work

### 🔌 Skill Ecosystem Compatibility

AuditronClaw supports both **OpenClaw skills** and **Claude Code skills**, so you can reuse rich skill resources from both ecosystems without rebuilding them.

### 🌟 Core Capabilities

| Capability | Description | Benefit |
|------|------|------|
| **🧠 Dual-watermark memory** | Long-term profile + short-term summaries that continuously learn user preferences | Understands you better over time and avoids repeated questions |
| **🔍 Full behavior auditing** | 5-category real-time event auditing, JSONL logs, and a Rich monitoring terminal | No more black boxes; every decision is traceable |
| **🛡️ Zero-trust execution** | `help` -> `run` two-phase calls that read the manual before execution | Reduces P0 incident risk by 80% (50% -> 10%) |
| **⏰ Heartbeat task engine** | Independent background process for scheduled tasks | Automates complex and repetitive tasks |
| **🖥️ Cross-platform support** | Adaptive support for Unix and Windows, with LLM-selected commands | One codebase runs across platforms |

---

## ✨ Features

### 🧠 Intelligent Core

- **Dual-watermark memory system**
  - Long-term profile (`user_profile.md`): user preferences, occupation, and special requirements
  - Recent summaries (SQLite): automatically summarizes every `MAX_TURNS` turns and keeps the latest `KEEP_TURNS` turns
  - Context trimming: preserves key conversations and prevents token explosion

- **Two-phase skill invocation**
  - `mode='help'`: read the full instruction file (`SKILL.md`)
  - `mode='run'`: execute the concrete operation
  - Supports reconsideration: after reading the manual, the agent can choose another tool

- **Transparent monitoring system**
  - 5 audited event types: `llm_input`, `tool_call`, `tool_result`, `ai_message`, `system_action`
  - JSONL log format with `tail -f` real-time monitoring
  - Rich terminal UI with colors and panels for different event types

- **Heartbeat task system**
  - Independent background process checks the task queue every second
  - Supports daily, weekly, and monthly recurring tasks
  - Persistent task storage survives restarts

### 🛡️ Security Sandbox

- **Cross-platform path interception**
  - Blocks unauthorized access on both Unix and Windows
  - Forbids `..`, absolute paths, and user home directory access
  - Restricts all operations to the `office/` workspace

- **Shell command safety**
  - Blocks dangerous commands with regex matching
  - Enforces a 60-second timeout circuit breaker
  - Runs commands non-interactively and requires flags such as `-y` when needed

### 🖥️ Cross-platform Capabilities

- **System information injection** - automatically detects the operating system and injects platform-specific context
- **LLM-selected commands** - generates suitable commands based on the platform, such as PowerShell or Bash
- **Path format compatibility** - automatically handles `/` and `\` path separators
- **Environment variable adaptation** - reads and sets environment variables across platforms

### 🔧 Built-in Tools

| Tool | Function | Example |
|------|------|------|
| `get_current_time` | Get the current time | "What time is it now?" |
| `calculator` | Math calculator | "What is 25 times 48?" |
| `schedule_task` | Scheduled tasks and alarms | "Remind me to drink water every morning at 8" |
| `list_scheduled_tasks` | List tasks | "What tasks do I have?" |
| `delete_scheduled_task` | Delete a task | "Cancel tomorrow's meeting reminder" |
| `modify_scheduled_task` | Modify a task | "Move the 8 o'clock meeting to 9" |
| `get_system_model_info` | Get model information | "What model are you?" |
| `save_user_profile` | Update user profile | "Remember that I like iced Americano" |
| `list_office_files` | List files | "Show me what is in office" |
| `read_office_file` | Read a file | "Read readme.txt" |
| `write_office_file` | Write a file | "Create test.py" |
| `execute_office_shell` | Run a shell command | "Run python test.py" |

### 🎯 Pluggable Skills

- **Dynamic loading**: automatically scans `workspace/office/skills/`
- **SKILL.md convention**: each skill contains a complete instruction manual
- **Compatible with OpenClaw and Claude Code skills**: reuse skills from both ecosystems directly
- **Recommended skills**:
  - `skill-creator`: create skills with natural language through AuditronClaw
  - `skill-vetter`: check skill safety
  - `mcporter`: connect external MCP (Model Context Protocol) services
  - `mcp-builder`: build your own MCP services
  - `tavily-search`: AI-optimized web search
  - `weather`: weather lookup

---

## 🚀 Quick Start

### 1️⃣ Installation

```bash
# Clone the project
git clone https://github.com/Canyon-Li/AuditronClaw.git
cd AuditronClaw

# Install dependencies and register the CLI in one step
pip install -e .
```

> 💡 **Virtual environment recommended**:
> ```bash
> # Create a virtual environment
> python3 -m venv venv
> source venv/bin/activate  # Windows: venv\Scripts\activate
>
> # Install the project. Dependencies from requirements.txt are installed automatically.
> pip install -e .
> ```
>
> After installation, the `auditronclaw` command is available from any directory.

### 2️⃣ Configuration

There are two configuration methods: the **automatic setup wizard** (recommended) and **manual configuration**.

#### Option 1: Automatic Setup Wizard (Recommended)

```bash
# Start the interactive configuration wizard
auditronclaw config
```

The wizard guides you through:
1. Choosing a model provider (OpenAI / Anthropic / Alibaba Cloud / Tencent / Z.AI / Ollama)
2. Entering an API key
3. Configuring the Base URL (optional)
4. **Automatically testing the connection** to verify the configuration

![Configuration Wizard](docs/config.png)

#### Option 2: Manual Configuration

```bash
# Copy the example configuration file
cp .env.example .env

# Edit the configuration file
vim .env  # Or use your preferred editor
```

Edit `.env` and configure the required parameters:

```bash
# Model provider
DEFAULT_PROVIDER=aliyun
DEFAULT_MODEL=glm-5

# API Key. Choose the corresponding key for your provider.
OPENAI_API_KEY=sk-your-api-key-here

# Base URL. Optional; configure it when using a proxy or compatible endpoint.
OPENAI_API_BASE=https://coding.dashscope.aliyuncs.com/v1
```

**Configuration reference:**
- `DEFAULT_PROVIDER`: model provider (`openai`, `anthropic`, `aliyun`, `tencent`, `z.ai`, `ollama`)
- `DEFAULT_MODEL`: model name, such as `gpt-4o-mini`, `glm-5`, or `qwen-max`
- `OPENAI_API_KEY`: API key for OpenAI or compatible APIs
- `ANTHROPIC_API_KEY`: Anthropic API key
- `OPENAI_API_BASE`: Base URL for compatible APIs such as Alibaba Cloud or Tencent Cloud
- `OLLAMA_BASE_URL`: local Ollama service URL, defaulting to `http://localhost:11434`

> 💡 **Workspace configuration**: the workspace path is initialized in code and defaults to the `workspace` folder in the project root. You do not need to configure it in `.env`. Set the `AUDITRONCLAW_WORKSPACE` environment variable only when you need a custom workspace path.

> 💡 Tip: after configuration, run `auditronclaw run` to test whether chat connectivity works.

### 3️⃣ Run

```bash
# Start the main program
auditronclaw run
```

![Welcome Screen](docs/welcome.png)

### 4️⃣ Basic Usage

After startup, AuditronClaw enters the interactive chat interface:

![Chat Interface](docs/chat.png)

**Common command examples:**

| Type | Example Command | Description |
|------|----------|------|
| ⏰ Time query | `What time is it now?` | Get the current time |
| 🧮 Math | `Calculate 25 times 48` | Use the calculator tool |
| ⏲️ Scheduled task | `Remind me to drink water every morning at 8` | Create a recurring task |
| 📋 List tasks | `What tasks do I have?` | View the task list |
| ✏️ Modify task | `Move the 8 o'clock water reminder to 9` | Modify an existing task |
| ❌ Delete task | `Cancel tomorrow's meeting reminder` | Delete a task |
| 📁 File operations | `Show me the files in office` | List workspace files |
| 📖 Read file | `Read readme.txt` | Read file content |
| 📝 Create file | `Create test.py` | Write a new file |
| 💻 Shell command | `Run python test.py` | Execute a shell command |
| 🚪 Exit | `/exit` | Exit the program |

### ⏰ Heartbeat Task System

AuditronClaw includes a heartbeat task system that automatically executes scheduled tasks in the background:

- **Automatic triggering**: the heartbeat process checks the task queue every second and triggers tasks on time
- **Recurring tasks**: supports daily, weekly, and monthly recurrence
- **Persistent tasks**: tasks are stored in `workspace/tasks.json` and survive restarts
- **Real-time monitoring**: run `auditronclaw monitor` to view task execution logs

**Heartbeat task example:**
```bash
# Create a recurring task
> Remind me to drink water every morning at 8
✅ Task added to queue | Recurrence: daily | First run: 2026-04-07 08:00:00

# The heartbeat system triggers the reminder at 8:00 every day
```

> 💡 Tip: heartbeat tasks run in the background and can execute even when the main program is not running, as long as the heartbeat process is started separately.

### 5️⃣ Monitoring Terminal

Run this in another terminal:
```bash
auditronclaw monitor
```

![Monitoring Terminal](docs/monitor.png)

---

## 🏢 Use Cases

### 🔒 Enterprise Applications
- **Compliance auditing** - 5-category event audit logs for enterprise compliance requirements
- **Permission control** - sandbox isolation and path interception prevent unauthorized operations
- **Task automation** - heartbeat task engine executes repetitive work on schedule
- **Knowledge accumulation** - dual-watermark memory continuously learns organizational preferences

### 🧪 AI Research and Development
- **Agent behavior analysis** - fully records LLM decision processes and tool-call chains
- **Security research** - two-phase invocation helps study AI safety boundaries
- **Debug-friendly workflow** - JSONL logs and a Rich monitoring terminal make issues easier to locate
- **Extensible architecture** - pluggable skills make it easy to validate new ideas

### 🖥️ Cross-platform Deployment
- **Windows** - full support for PowerShell and CMD with automatic path adaptation
- **Linux** - native support for all distributions and Bash compatibility
- **macOS** - supports zsh/bash and integrates naturally with Unix toolchains

### 🛠️ Developer Tools
- **Local development assistant** - file operations and shell execution for automated coding tasks
- **Project monitoring** - monitor AI behavior in real time to prevent unexpected operations
- **Skill development** - supports custom skills for fast tool integration
- **MCP service integration** - connects external MCP services to extend capability boundaries

### 📚 Education and Learning
- **AI agent teaching** - transparently demonstrates agent architecture and decision flows
- **Prompt engineering** - observe how different prompts affect AI behavior
- **Security practice** - learn AI safety best practices and protective measures
- **Open-source contribution** - participate in open-source development and gain practical experience

### 🏠 Personal Productivity
- **Smart schedule management** - reminders and recurring tasks reduce manual effort
- **File automation** - batch-process files and automate workflows
- **Information lookup** - integrate search skills for quick information retrieval
- **Personalized assistant** - memory learns personal preferences and improves over time

---

## 🏗️ System Architecture

### Full Architecture Diagram

![System Architecture](docs/architect.png)

**Architecture overview**:

- **Input layer** (blue): heartbeat tasks + user input -> gateway
- **Memory layer** (pink): context trimming + long-term and short-term memory management
- **Intelligent decision layer** (yellow): Agent Loop + LLM reasoning and decisions
- **Tool execution layer** (purple): built-in tools + pluggable skills
- **Security layer** (orange): path access interception + cross-platform compatibility
- **Transparent monitoring layer** (green): memory updates + tool decisions + tool parameters + call results
- **Output layer** (bottom): chat terminal + monitoring terminal

### Core Modules

| Module | File | Function |
|------|------|------|
| **Agent loop** | `auditronclaw/core/agent.py` | LangGraph StateGraph and decision engine |
| **Skill loading** | `auditronclaw/core/skill_loader.py` | Dynamically loads SKILL.md with two-phase invocation |
| **Context management** | `auditronclaw/core/context.py` | Message trimming and dual-watermark memory |
| **Built-in tools** | `auditronclaw/core/tools/builtins.py` | Time, calculation, task scheduling, and more |
| **Sandbox tools** | `auditronclaw/core/tools/sandbox_tools.py` | File operations and shell execution |
| **Audit logging** | `auditronclaw/core/logger.py` | JSONL event logging |
| **Heartbeat tasks** | `auditronclaw/core/heartbeat.py` | Scheduled task checking and triggering |

### Project Structure

```
AuditronClaw/
├── auditronclaw/                    # Core package
│   ├── core/
│   │   ├── agent.py              # Agent loop
│   │   ├── config.py             # Configuration management
│   │   ├── context.py            # Context trimming
│   │   ├── provider.py           # LLM provider adapters
│   │   ├── skill_loader.py       # Dynamic skill loading
│   │   ├── logger.py             # Audit logging
│   │   ├── heartbeat.py          # Heartbeat tasks
│   │   └── tools/
│   │       ├── base.py           # Tool decorator
│   │       ├── builtins.py       # Built-in tools
│   │       └── sandbox_tools.py  # Sandbox tools
│   └── __init__.py
├── workspace/
│   ├── office/                   # Sandbox workspace
│   │   ├── skills/               # Pluggable skills
│   │   │   ├── weather/
│   │   │   ├── skill-creator/
│   │   │   └── ...
│   │   └── .env                  # Environment variables
│   ├── memory/
│   │   └── user_profile.md       # Long-term user profile
│   ├── state.sqlite3             # Conversation history database
│   └── tasks.json                # Scheduled task queue
├── logs/
│   └── local_geek_master.jsonl   # Audit logs
├── docs/                         # Documentation and diagrams
│   ├── architect.png             # System architecture diagram
│   ├── monitor.png               # Monitoring terminal screenshot
│   ├── welcome.png               # Welcome screen
│   ├── chat.png                  # Chat interface
│   ├── config.png                # Configuration wizard
│   ├── memory.png                # Memory system
│   └── context_cut.png           # Context trimming
├── entry/
│   ├── main.py                   # Main program entry
│   ├── cli.py                    # CLI configuration wizard
│   └── monitor.py                # Monitoring terminal
├── tests/                        # Test suite
│   ├── test_agent.py
│   ├── test_builtins.py
│   ├── test_two_phase_skills.py  # Two-phase tests
│   └── logs/                     # Test reports
├── setup.py
├── .env                          # Runtime environment configuration
├── .env.example                  # Example environment configuration
└── README.md
```

---

## 📖 User Guide

### Configuration Files

**`.env` file**: the main configuration file that contains sensitive information such as API keys and model settings.

**`.env.example` file**: configuration template with descriptions and example values for all available options.

For first-time setup, copy the example file and modify it:
```bash
cp .env.example .env
```

See [Quick Start - Configuration](#2️⃣-configuration) for detailed configuration instructions.

### Skill System

#### Installing Skills

**Method 1: Direct copy**
```bash
cp -r /path/to/skill workspace/office/skills/
```

**Method 2: Use skill-creator**
```bash
# Install the skill-creator skill first
cd workspace/office/skills
git clone https://github.com/.../skill-creator.git

# Then ask AuditronClaw to create a new skill with natural language
> Create a skill for querying Bitcoin prices
```

**Method 3: Use skill-vetter for safety checks**
```bash
# Install skill-vetter
cd workspace/office/skills
git clone https://github.com/.../skill-vetter.git

# Ask AuditronClaw to check skill safety
> Check whether the weather skill is safe
```

#### Skill Convention

Each skill contains a `SKILL.md` file:

````markdown
---
name: weather
description: Get weather forecasts
---

# Weather Skill

## Function
Get real-time weather forecasts for cities worldwide.

## Command Example
```bash
curl "wttr.in/Beijing?format=3"
```

## Parameters
- City name (required)
- Number of days (optional)
````

### Scheduled Tasks

```bash
# One-time task
> Wake me up tomorrow morning at 9

# Recurring tasks
> Remind me to drink water every morning at 8
> Hold a team meeting every Monday at 10 AM

# View tasks
> What tasks do I have?

# Modify a task
> Move the 8 o'clock water reminder to 9

# Delete a task
> Cancel tomorrow's meeting reminder
```

### Advanced Usage

#### 1. Use the Monitor

Run this in another terminal:
```bash
auditronclaw monitor
```

View in real time:
- 🧠 LLM input
- 💡 Tool calls
- 💻 Tool results
- 🤖 AI replies
- ⚙️ System actions

#### 2. View Audit Logs

```bash
# Real-time monitoring
tail -f logs/local_geek_master.jsonl

# Search for specific events
grep "tool_call" logs/local_geek_master.jsonl | tail -20
```

#### 3. Customize the User Profile

Edit `workspace/memory/user_profile.md`:

```markdown
# User Profile

- **Name**: Thor Allen
- **Occupation**: Programmer
- **Preferences**:
  - Likes iced Americano
  - Often writes code in Python
  - Gets up at 8 every day
- **Special requirements**:
  - Keep answers concise
  - Do not use emojis
```

---

## 🧠 Memory System

### Dual-watermark Memory Architecture

![Memory System](docs/memory.png)

- **Long-term memory**: `user_profile.md`, a Markdown file that stores user preferences, occupation, and special requirements
- **Short-term memory**: SQLite database that stores complete conversation history
- **Automatic summarization**: triggers every 20 conversation turns and keeps the latest 10 turns

### Context Trimming

![Context Trimming](docs/context_cut.png)

When the number of conversation turns exceeds the threshold:
1. System messages are always retained
2. The latest N full conversation turns are retained
3. Older conversations are compressed into summaries
4. Token explosion is prevented

### Turn Memory

![Turn Memory](docs/turn_memory.png)

Each complete turn contains:
- User message (`HumanMessage`)
- AI response (`AIMessage`)
- Tool call (`ToolMessage`)

---

## 🧪 Testing

### Run Tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run a specific test
python3 tests/test_two_phase_skills.py

# Run two-phase tests
python3 -c "from tests.test_two_phase_skills import run_tests; run_tests()"
```

### Test Coverage

| Test File | Coverage | Status |
|---------|---------|------|
| `test_agent.py` | Agent loop | ✅ Passing |
| `test_builtins.py` | Built-in tools | ✅ Passing |
| `test_context.py` | Context trimming | ✅ Passing |
| `test_sandbox_tools.py` | Sandbox tools | ✅ Passing |
| `test_two_phase_skills.py` | Two-phase invocation | ✅ Passing |
| `test_heartbeat.py` | Heartbeat tasks | ✅ Passing |

### Two-phase Test Report

Based on the experimental data in `tests/logs/test_two_phase_skills.md`:

| Metric | Single-phase | Two-phase | Improvement |
|------|--------|--------|------|
| **Safety hit rate** | 50.0% | 90.0% | **+40%** |
| **P0 incident rate** | 50.0% | 10.0% | **-80%** |
| **Average decision latency** | 19.33s | 23.88s | +23.5% |

**Conclusion**: the two-phase architecture trades 23.5% more time for an **incident-rate drop from 50% to 0%** in actual destructive execution.

---

## 🤝 Contributing

Issues and pull requests are welcome.

### Development Environment

```bash
# Clone the project
git clone https://github.com/Canyon-Li/AuditronClaw.git
cd AuditronClaw

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install development dependencies
pip install -e ".[dev]"
```

### Commit Convention

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation update
- `style:` code style
- `refactor:` refactoring
- `test:` test-related changes
- `chore:` build or tooling changes

---

## 📄 License

MIT License

---

## 🙏 Acknowledgements

- **[OpenClaw](https://github.com/openclaw/openclaw)** - inspiration and skill ecosystem
- **LangChain** - LLM application development framework
- **LangGraph** - stateful agent construction
- **Rich** - terminal styling
- **Prompt Toolkit** - interactive command line
- **All contributors** - thank you for your contributions!

---

## 📬 Contact

- **GitHub**: [@Canyon-Li](https://github.com/Canyon-Li)
- **Email**: allen.wtyummy@gmail.com

---

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Canyon-Li/AuditronClaw&type=Date)](https://star-history.com/#Canyon-Li/AuditronClaw&Date)

---

<div align="center">

**👾 AuditronClaw · Next-Gen Transparent Agent Architecture**

Made with ❤️ by [@Canyon-Li](https://github.com/Canyon-Li)

</div>
