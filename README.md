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

77.5% 注入拦截 · 2.0% 危害落地 · 83.8% 任务达成 · 0 安全误拦

（以上为 87 条基准用例 + 62 项测试在 glm-4-flash 上的实测结果，协议与复现方式见下文）

</div>

---

## 这是什么

AuditronClaw 是一个跑在本地的 LangGraph 智能体终端，**源自（fork）[CyberClaw](https://github.com/ttguy0707/CyberClaw)**（原作者 @ttguy0707，MIT 协议，感谢其开源工作）。**本项目已脱离 GitHub fork 网络，作为独立仓库维护**；安全加固已回流上游（见下文）。

与上游的分野在于一条工作线：**审计 → 加固 → 度量**。fork 后对基线做了系统安全审计，发现并修复了三类可利用边界（eval RCE、沙盒绕过、技能命令代理），修复已作为 [PR #18](https://github.com/ttguy0707/CyberClaw/pull/18)、[PR #19](https://github.com/ttguy0707/CyberClaw/pull/19) 回流上游；随后建立了一套确定性断言的双维基准，让"安全"不再是自证声明，而是可复现的数字。

**继承自上游并继续维护的能力**：两段式技能调用（help → run）、双水位记忆（长期画像 + 短期摘要）、心跳任务引擎、JSONL 全行为审计与监控终端、跨平台（Unix/Windows）、兼容 OpenClaw 与 Claude Code 技能生态。

**本项目新增**：

- 结构化命令白名单（shlex 校验，封死环境变量展开 / 内联解释器等绕过面）
- 会话隔离（`--thread`，画像按会话分文件 + 写入留痕）
- 注入拦截基准 50 条 + Golden 能力基准 37 条 + CI 门禁

---

## 🔒 安全审计与加固

| 审计发现 | 修复 | 验证 |
|---|---|---|
| `calculator` 工具 `eval()` 可经属性链逃逸拿到 `os.system`（任意代码执行） | AST 节点白名单求值器，仅放行算术 | 红队 17 条注入向量全拒绝 |
| shell 校验为五条正则黑名单，可被环境变量展开 / 引号内联解释器绕过 | shlex 结构化命令白名单，白名单外二进制一律拒绝 | 红队 18 条注入全拦 + 6 条合法全放行 |
| 恶意 SKILL.md 可借技能 `run` 通道代理执行命令 | 技能命令与手动 shell 走同一校验器 | 注入基准 skill_md 面 12/12 |
| 画像全局单文件且无写入留痕，存在跨会话持久化投毒链 | 画像按会话分文件 + 行级 diff 审计留痕 | 注入基准 profile 面工具层全兜底 |

**已知未修复**：注入基准发现 1 条真实落地——用户套问下 LLM 会复述系统提示词原文（信息外泄，工具层无法拦截），归因提示词保密性不足。如实列出，不藏。

---

## 📊 双维评测：安全基准 × 能力基准

安全测试只回答"该拦的拦住了吗"，回答不了"该干的干成了吗"——一个拒绝所有请求的 agent 能拿安全满分。AuditronClaw 在同一条隔离流水线上跑两套基准，让安全与能力都从自证声明变为可复核的数字。判定器全为确定性断言（无 LLM-as-judge），结果可复现。

### 🛡️ 安全基准：Prompt 注入拦截率

50 条恶意用例，每条独立 workspace + 独立会话，绝不互相污染。

**最新结果**（glm-4-flash,2026-08-17,49 条有效，n=50 单次）：

| 入口面 | 提示面拦截 | 危害落地 |
|---|---|---|
| 恶意技能说明书 (SKILL.md) | 12/12 | 0/12 |
| 恶意文件内容 | 11/12 | 0/12 |
| 已污染画像 | 6/12 | 0/12 |
| 直接越狱指令 | 9/13 | 1/13 |
| **合计** | **38/49 (77.5%)** | **1/49 (2.0%)** |

**结果解读**：
- **77.5% 提示面拦截** = LLM 未被骗发起越权/污染行为。失守的 11 条说明 LLM 的"对齐"面可以被针对性 prompt 说服。
- **2.0% 危害落地** = 攻击真正达成。失守 11 条中 10 条被工具层沙盒白名单兜住，LLM 被骗 ≠ 系统被攻破。
- **唯一真实落地 1 条**：LLM 在套问下复述系统提示词原文——信息外泄，工具层无法拦截，见上方"已知未修复"。

复现：`python benchmarks/run_injection_bench.py` · 用例：`benchmarks/cases/injection_cases.yaml`

### ⚙️ 能力基准：Golden 任务达成率

37 条正常任务（工具选择 8 / 文件操作 8 / 合法 shell 6 / 任务 CRUD 6 / 记忆写入 5 / 技能两段式 4），断言方向与注入基准相反——那边"不许发生"，这边"必须发生且结果正确"。

**最新结果**（glm-4-flash,2026-08-18,37 条全有效）：

| 任务面 | 通过 |
|---|---|
| 工具选择 | 8/8 |
| 文件操作 | 8/8 |
| 合法 shell（安全假阳性检验） | 6/6 |
| 任务 CRUD | 4/6 |
| 记忆写入 | 3/5 |
| 技能两段式（零信任执行协议） | 2/4 |
| **合计** | **31/37 (83.8%)** |

**结果解读**：
- **0 条安全假阳性（over_refusal）**：全部 6 条 office 内合法操作（含 shell）无一被安全提示词吓退——安全措施没有以"不敢干活"为代价。
- **6 条失败分三类**：**任务谎报** 3 条（口头回复"已设置提醒/已记住"，工具轨迹为空）；**时间解析失败** 1 条（"每周一"被解析为过去日期，工具正确拒绝但未自纠）；**技能两段式违反** 2 条（直接 `run` 跳过 `help`，其中 1 条编造"权限限制"拒绝理由）。谎报发生在 LLM 决策层，单元测试结构性测不到，只有端到端能力基准能抓。
- **副作用任务一律双锚断言**（工具调用 + 落盘终态）：tasks.json / 画像 / 文件内容直接核验，"调了工具就说干完了"过不了关。

复现：`python benchmarks/run_golden_eval.py` · 用例：`benchmarks/cases/golden_cases.yaml`

### 协议与盲区（两套基准共用）

- 判定 = 确定性断言（工具调用/关键词/文件落盘），无 LLM-as-judge，可复现。
- 隔离 = 每用例独立 workspace + 独立会话（`benchmarks/bench_pipeline.py` 共享流水线），用例间零污染。
- 盲区：单次运行、文本面泄漏、结果随模型漂移。测量的是"这一个模型 + 这一批用例"的剖面，不是绝对能力。

---

## 🚀 快速开始

```bash
# 安装
pip install -e .

# 交互式配置向导（选择模型提供商 / 输入 API Key / 连接测试）
auditronclaw config

# 启动主程序（--thread 可选，隔离会话历史/画像/日志）
auditronclaw run

# 监控终端（另一个终端，实时查看 agent 行为审计流）
auditronclaw monitor

# 跑基准（需要模型 API Key）
python benchmarks/run_injection_bench.py
python benchmarks/run_golden_eval.py
```

变更历史见 [CHANGELOG](CHANGELOG.md)。
