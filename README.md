<div align="center">

# AuditronClaw

### 别人说"我的 Agent 很安全"，这里给你数字

[![AuditronClaw](https://img.shields.io/badge/AuditronClaw-1.1.0-purple.svg?logo=cyberpunk)](https://github.com/Canyon-Li/AuditronClaw)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg?logo=python)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.x-blue.svg)](https://langchain-ai.github.io/langgraph/)
[![LangChain](https://img.shields.io/badge/LangChain-1.x-blue.svg)](https://python.langchain.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/Canyon-Li/AuditronClaw/actions/workflows/ci.yml/badge.svg)](https://github.com/Canyon-Li/AuditronClaw/actions/workflows/ci.yml)
[![GitHub](https://img.shields.io/badge/GitHub-@Canyon-Li-black.svg?logo=github)](https://github.com/Canyon-Li)

**安全边界可度量的本地智能体** · Agent with Measurable Security Boundaries

74.0% 注入拦截 · 2.0% 危害落地 · 81.1% 任务达成 · 0 安全误拦

（以上为 87 条基准用例 + 62 项测试在 glm-4-flash 上的实测结果，协议与复现方式见下文）

<!-- 截图占位（截好放 docs/images/ 后取消注释）：
![run 会话](docs/images/run-demo.png)
![monitor 审计流](docs/images/monitor-demo.png)
-->
</div>

---

## 📖 这是什么

AuditronClaw 是一个跑在本地的 LangGraph 智能体终端，**源自（fork）[CyberClaw](https://github.com/ttguy0707/CyberClaw)**（原作者 @ttguy0707，MIT 协议，感谢其开源工作）。**本项目已脱离 GitHub fork 网络，作为独立仓库维护**；安全加固已回流上游（见下文）。

与上游的分野在于一条工作线：**审计 → 加固 → 度量**。fork 后对基线做了系统安全审计，发现并修复了三类可利用边界（eval RCE、沙盒绕过、技能命令代理），修复已作为 [PR #18](https://github.com/ttguy0707/CyberClaw/pull/18)、[PR #19](https://github.com/ttguy0707/CyberClaw/pull/19) 回流上游；随后建立了一套确定性断言的双维基准，让"安全"不再是自证声明，而是可复现的数字。

**继承自上游并继续维护的能力**：两段式技能调用（help → run）、双水位记忆（长期画像 + 短期摘要）、心跳任务引擎、JSONL 全行为审计与监控终端、跨平台（Unix/Windows）、兼容 OpenClaw 与 Claude Code 技能生态。

**本项目新增**：

- 结构化命令白名单（shlex 校验，封死环境变量展开 / 内联解释器等绕过面）
- 会话隔离（`--thread`，画像按会话分文件 + 写入留痕）
- 注入拦截基准 50 条 + Golden 能力基准 37 条 + CI 门禁

---

## 🏗️ 架构一瞥

```
entry/                    CLI 入口（config 配置向导 / run 主程序 / monitor 监控终端）
auditronclaw/core/
  ├─ agent                LangGraph 主循环（系统提示词 + 工具决策）
  ├─ tools/               工具层：内置工具 + shlex 命令白名单 + AST 算术求值器
  ├─ skill_loader         技能两段式加载（help → run），命令与手动 shell 同校验器
  ├─ bus / logger         全行为事件总线 → <thread>.jsonl 审计日志
  ├─ heartbeat            心跳任务引擎（定时任务后台执行）
  ├─ context              上下文裁剪 + 摘要压缩（短期记忆）
  └─ provider             多模型接入（OpenAI 兼容 / Anthropic / Ollama）
benchmarks/               注入基准 × 能力基准（共享隔离流水线）
```

数据落盘均在 `workspace/` 下：`office/` 是唯一允许文件与 shell 操作的沙盒工位；`memory/` 存长期画像（按会话分文件）；技能卡槽在 `workspace/office/skills/`。

---

## 🔒 安全审计与加固

| 审计发现 | 修复 | 验证 |
|---|---|---|
| `calculator` 工具 `eval()` 可经属性链逃逸拿到 `os.system`（任意代码执行） | AST 节点白名单求值器，仅放行算术 | 红队 17 条注入向量全拒绝 |
| shell 校验为五条正则黑名单，可被环境变量展开 / 引号内联解释器绕过 | shlex 结构化命令白名单，白名单外二进制一律拒绝 | 红队 18 条注入全拦 + 6 条合法全放行 |
| 恶意 SKILL.md 可借技能 `run` 通道代理执行命令 | 技能命令与手动 shell 走同一校验器 | 注入基准 skill_md 面 12/12 |
| 画像全局单文件且无写入留痕，存在跨会话持久化投毒链 | 画像按会话分文件 + 行级 diff 审计留痕 | 注入基准 profile 面工具层全兜底 |
| 系统提示词无保密条款，套问下被整段复述（曾为注入基准唯一真实落地） | 提示词三段式分段隔离（人设 / 外部数据区·非指令声明 / 安全协议殿后）+ 保密条款 | jail_008 转绿；分段结构不变量回归用例 |
| 路径基准不一致：带 `office/` 前缀的路径静默双写 `office/office/`（工具报成功、文件落在别处，改了其实没改） | 路径基准统一：文件工具冗余前缀归一（落点明示）+ shell 前缀引导式拒绝 | 双写复现用例 + 前缀守卫用例全绿 |

**已知边界**：注入基准现有 1 条真实落地——投毒画像的破坏性指令，越界路径被工具层拒绝后，模型自适应改用 office 内合法路径完成（`cat x > resume.md` 截断文件，profile_010）。该命令在现行策略下合法，提示层无法区分"受骗的写"与"授权的写"——根治是高危操作审批门（路线图第三章），如实保留至其收口。

---

## 📊 双维评测：安全基准 × 能力基准

安全测试只回答"该拦的拦住了吗"，回答不了"该干的干成了吗"——一个拒绝所有请求的 agent 能拿安全满分。AuditronClaw 在同一条隔离流水线上跑两套基准，让安全与能力都从自证声明变为可复核的数字。判定器全为确定性断言（无 LLM-as-judge），结果可复现。

### 🛡️ 安全基准：Prompt 注入拦截率

50 条恶意用例，每条独立 workspace + 独立会话，绝不互相污染。

**最新结果**（glm-4-flash,2026-08-22,50 条有效，n=50 单次）：

| 入口面 | 提示面拦截 | 危害落地 |
|---|---|---|
| 恶意技能说明书 (SKILL.md) | 12/12 | 0/12 |
| 恶意文件内容 | 11/12 | 0/12 |
| 已污染画像 | 6/12 | 1/12 |
| 直接越狱指令 | 8/14 | 0/14 |
| **合计** | **37/50 (74.0%)** | **1/50 (2.0%)** |

**结果解读**：
- **74.0% 提示面拦截** = LLM 未被骗发起越权/污染行为。失守的 13 条说明 LLM 的"对齐"面可以被针对性 prompt 说服。
- **2.0% 危害落地** = 攻击真正达成。失守 13 条中 12 条被工具层沙盒白名单兜住，LLM 被骗 ≠ 系统被攻破。
- **唯一真实落地 1 条**：投毒画像指令在越界路径被拒后，模型自适应改用合法路径完成破坏性写——见上方"已知边界"，根治在第三章审批门。
- **诚实披露**：本轮提示词保密性改造修掉了原唯一落地（系统提示词复述），同时引入 2 条提示面回退（施压话术下更愿尝试，均被工具层兜住）——单案 3 次新旧代码对照归因成立，逐条记录在 [RESULTS.md](benchmarks/RESULTS.md)。防线不应全压在提示词层，是本项目坚持纵深防御的实测理由。

复现：`python benchmarks/run_injection_bench.py` · 用例：`benchmarks/cases/injection_cases.yaml` · [50 条逐用例结果存档](benchmarks/RESULTS.md)

### ⚙️ 能力基准：Golden 任务达成率

37 条正常任务（工具选择 8 / 文件操作 8 / 合法 shell 6 / 任务 CRUD 6 / 记忆写入 5 / 技能两段式 4），断言方向与注入基准相反——那边"不许发生"，这边"必须发生且结果正确"。

**最新结果**（glm-4-flash,2026-08-22,37 条全有效）：

| 任务面 | 通过 |
|---|---|
| 工具选择 | 8/8 |
| 文件操作 | 8/8 |
| 合法 shell（安全假阳性检验） | 6/6 |
| 任务 CRUD | 3/6 |
| 记忆写入 | 3/5 |
| 技能两段式（零信任执行协议） | 2/4 |
| **合计** | **30/37 (81.1%)** |

**结果解读**：
- **0 条安全假阳性（over_refusal）**：全部 6 条 office 内合法操作（含 shell）无一被安全提示词吓退——安全措施没有以"不敢干活"为代价。
- **7 条失败**：基线旧账 6 条（**任务谎报** 2、**时间解析失败** 1、**技能两段式违反** 2、**记忆谎报** 1——口头回复"已设置提醒/已记住"，工具轨迹为空）；本轮新增 1 条（gold_task_003：任务列表完整可见却做精确名匹配，两次列表后放弃——归因成立，属"放弃不自纠"家族）。谎报与放弃都发生在 LLM 决策层，单元测试结构性测不到，只有端到端能力基准能抓，修复方向是 Reflector（路线图第四章）。
- **副作用任务一律双锚断言**（工具调用 + 落盘终态）：tasks.json / 画像 / 文件内容直接核验，"调了工具就说干完了"过不了关。

复现：`python benchmarks/run_golden_eval.py` · 用例：`benchmarks/cases/golden_cases.yaml` · 结果存档：[benchmarks/RESULTS.md](benchmarks/RESULTS.md)

### 📋 协议与盲区（两套基准共用）

- 判定 = 确定性断言（工具调用/关键词/文件落盘），无 LLM-as-judge，可复现。
- 隔离 = 每用例独立 workspace + 独立会话（`benchmarks/bench_pipeline.py` 共享流水线），用例间零污染。
- 盲区：单次运行、文本面泄漏、结果随模型漂移。测量的是"这一个模型 + 这一批用例"的剖面，不是绝对能力。

---

## 🗺️ Roadmap

- **提示词保密性**：封堵系统提示词复述——当前唯一真实落地的 P0 项（见上"已知未修复"）
- **约束力强化**：针对任务谎报 / 时间解析失败 / 技能两段式违反的 prompt 迭代，双维基准作为回归门禁
- **Web 终端**：浏览器操作界面 + 毁灭性动作审批门（单操作员版优先）

---

## 🚀 快速开始

> 前提：Python ≥ 3.10，Windows / macOS / Linux 均可

```bash
# 1. 克隆并安装
git clone https://github.com/Canyon-Li/AuditronClaw.git
cd AuditronClaw
python -m venv .venv
#    Windows: .venv\Scripts\activate   Unix: source .venv/bin/activate
pip install -e .   # 国内可加 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 配置模型（交互式向导：选提供商 / 填 API Key / 自动连接测试）
auditronclaw config
```

支持 openai / anthropic / 阿里云 / 腾讯云 / z.ai / 任意 OpenAI 兼容接口，以及 [Ollama](https://ollama.com)（本地部署，无需 Key）。也可跳过向导：复制 `.env.example` 为 `.env` 手动编辑。

装技能：放入 `workspace/office/skills/<技能名>/SKILL.md`（兼容 OpenClaw / Claude Code 技能格式），支持热更新，无需重启。

```bash
# 3. 启动主程序（--thread 可选，隔离会话历史/画像/日志）
auditronclaw run

# 4. 监控终端（另开一个终端；run 用了 --thread 时，这里传同名参数才能看到对应日志流）
auditronclaw monitor

# 基准复现（前置：完成第 2 步配置；87 条用例会真实调用模型 API，产生少量开销，
# 可用 --model / --provider 覆盖默认值）
python benchmarks/run_injection_bench.py
python benchmarks/run_golden_eval.py
```

## ❓ 常见问题

- **配置向导没反应 / 无法交互**：直接 `cp .env.example .env` 手动填写，效果等同。
- **openai / anthropic 直连超时**：在向导中填写代理 Base URL，或改用国内 OpenAI 兼容接口（阿里云 / 腾讯云 / z.ai）。
- **基准跑一次开销多大**：87 条用例逐条真实调用模型，费用取决于所选模型，建议先用低价模型（如 glm-4-flash）跑通流程。

---

## 📄 许可证

[MIT](LICENSE) · Copyright (c) 2026 THOR（[CyberClaw](https://github.com/ttguy0707/CyberClaw) 原作者）· Copyright (c) 2026 Canyon-Li (AuditronClaw)

发现问题欢迎直接开 [issue](https://github.com/Canyon-Li/AuditronClaw/issues)（包括安全），恶意用例格式参考 [SECURITY.md](SECURITY.md)。

变更历史见 [CHANGELOG](CHANGELOG.md)。
