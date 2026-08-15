<div align="center">

# AuditronClaw

###  **当 AI 开始"黑箱操作"，你需要一双透视眼**

[![AuditronClaw](https://img.shields.io/badge/AuditronClaw-1.0.0-purple.svg?logo=cyberpunk)](https://github.com/Canyon-Li/AuditronClaw)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg?logo=python)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.x-blue.svg)](https://langchain-ai.github.io/langgraph/)
[![LangChain](https://img.shields.io/badge/LangChain-1.x-blue.svg)](https://python.langchain.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
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
