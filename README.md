<div align="center">

# AuditronClaw

###  **当 AI 开始"黑箱操作"，你需要一双透视眼**

[![AuditronClaw](https://img.shields.io/badge/AuditronClaw-1.0.0-purple.svg?logo=cyberpunk)](https://github.com/Canyon-Li/AuditronClaw)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg?logo=python)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.x-blue.svg)](https://langchain-ai.github.io/langgraph/)
[![LangChain](https://img.shields.io/badge/LangChain-1.x-blue.svg)](https://python.langchain.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/Canyon-Li/AuditronClaw/actions/workflows/ci.yml/badge.svg)](https://github.com/Canyon-Li/AuditronClaw/actions/workflows/ci.yml)
[![GitHub](https://img.shields.io/badge/GitHub-@Canyon-Li-black.svg?logo=github)](https://github.com/Canyon-Li)

**下一代透明智能体架构** · Next-Gen Transparent Agent Architecture

</div>

---

> 🤖 **你的 AI 在背着你做什么？AuditronClaw 让所有行为无所遁形**
>
> 💡 **灵感来源**：受 [OpenClaw](https://github.com/openclaw/openclaw) 的启发，AuditronClaw 专注于解决 AI 智能体的透明度和可控性问题。
>
> 🙏 **致谢**：本项目 fork 自 [CyberClaw](https://github.com/ttguy0707/CyberClaw)（原作者 @ttguy0707），基于 MIT 许可证进行二次开发。原始版权见 [LICENSE](LICENSE)。

---

## 📖 简介

AuditronClaw 是一个**透明可控智能体**，重新定义 AI 系统的可信边界：

- **🔍 白盒化决策** → 5 类事件审计 + JSONL 日志 + Rich 监控终端，所有行为可追溯
- **🛡️ 零信任执行** → 两段式调用（help → run），先看说明书再执行
- **🧠 持续学习** → 双水位记忆系统（长期画像 + 短期摘要），越用越懂你
- **⚡ 复杂任务编排** → 心跳任务系统 + 可插拔技能，解放双手

### 🔌 技能生态兼容

AuditronClaw 支持**OpenClaw 技能**和**Claude Code 技能**，可直接使用两个生态系统的丰富技能资源，无需重新开发。

### 🌟 核心能力

| 能力 | 说明 | 优势 |
|------|------|------|
| **🧠 双水位记忆** | 长期画像 + 短期摘要，持续学习用户偏好 | 越用越懂你，避免重复询问 |
| **🔍 全行为审计** | 5 类事件实时审计，JSONL 日志 + Rich 监控终端 | 告别黑箱，所有决策可追溯 |
| **🛡️ 零信任执行** | help → run 两段式调用，先看说明书再执行 | 先理解，再执行 |
| **⏰ 心跳任务引擎** | 后台独立进程，自动执行定时任务 | 解放双手，复杂任务自动化 |
| **🖥️ 跨平台支持** | Unix + Windows 双平台自适应，LLM 自主选择命令 | 一套代码，全平台运行 |

---

## 🛡️ 安全基准：Prompt 注入拦截率

AuditronClaw 用**行为断言**（非 LLM 判分）在隔离环境实测对 Prompt 注入的拦截能力——50 条恶意用例，每条独立 workspace + 独立会话，绝不互相污染。

**最新结果**（glm-4-flash,2026-08-17,49 条有效，n=50 单次）：

| 入口面 | 提示面拦截 | 危害落地 |
|---|---|---|
| 恶意技能说明书 (SKILL.md) | 12/12 | 0/12 |
| 恶意文件内容 | 11/12 | 0/12 |
| 已污染画像 | 6/12 | 0/12 |
| 直接越狱指令 | 9/13 | 1/13 |
| **合计** | **38/49 (77.5%)** | **1/49 (2.0%)** |

**结果解读（诚实版）**：
- **77.5% 提示面拦截** = LLM 未被骗发起越权/污染行为。失守的 11 条说明 LLM 的"对齐"面可以被针对性 prompt 说服。
- **2.0% 危害落地** = 攻击真正达成。失守 11 条中 10 条被工具层沙盒白名单兜住（`read_office_file ../../.env` → 越权拦截、`cat /etc/passwd` → 权限拒绝），LLM 被骗 ≠ 系统被攻破。
- **唯一真实落地 1 条**：LLM 在用户套问下**复述了系统提示词原文**（含沙盒边界细节）——信息外泄，工具层无法拦截。这是当前基准发现的唯一 P0 级缺陷，归因于提示词保密性不足。

**协议与盲区**：
- 判定 = 行为断言（工具调用 / 泄密关键词），无 LLM-as-judge，可复现。
- 盲区：文本面泄漏（如上）、单次运行、结果随模型漂移。测量的是"这一个模型 + 这 50 条"的剖面，不是绝对安全。

复现：`python benchmarks/run_injection_bench.py`（JSONL 落盘 `benchmarks/results/`）。用例：`benchmarks/cases/injection_cases.yaml`。

---

## 🚧 文档建设中

完整文档（快速开始、配置向导、架构图、使用指南、测试报告）正在随安全加固与评估管线工作同步重写中。

当前可用的入口：

```bash
# 安装
pip install -e .

# 交互式配置向导（选择模型提供商 / 输入 API Key / 连接测试）
auditronclaw config

# 启动主程序
auditronclaw run

# 监控终端（另一个终端）
auditronclaw monitor
```

开发计划见 [CHANGELOG](CHANGELOG.md)。
