# ADR-001: MCP 不做一等公民

日期：2026-08-23 · 状态：已接受

## 背景

MCP（Model Context Protocol）是模型与外部工具互操作的行业事实标准。对本项目而言，工具层不是基础设施，而是**被审计对象**：审计埋点位于图节点层（`agent_node` 对每次 `tool_call` / `tool_result` 落盘），命令白名单与路径防护则实现在各内置工具体内（`sandbox_tools.py`）。生态侧，`langchain-mcp-adapters` 可将任意 MCP server 的工具转换为 LangChain 工具注入主循环——接入在技术上是现成的。需要决策的不是"能不能接"，而是 MCP 在本项目中处于什么地位。

## 决策

MCP 不做一等公民：不引入 MCP SDK，不将自身工具暴露为 MCP server。外部能力的接入通道为：

1. **LangChain 工具接口**（库 API）：`create_agent_app(tools=...)` 整体替换，`extra_tools` 追加注入（随配套变更引入）——内置工具全保留，外接工具按个追加；
2. **SKILL.md 技能层**（零代码）：置入 skills 目录即注册，热更新，`run` 模式汇入同一命令白名单校验器。

## 备选与否决理由

- **完整 MCP 网关（一等公民）**：当前阶段等于为未出现的需求建基建；更关键的是，MCP server 是独立进程——工具执行移出主进程后，"防线在构造上不可绕过"退化为"信任第三方代码自觉合规"，与项目主线（应用层防线的正确性依赖代码每一次都不出错，故要构造性边界）相悖。
- **不接入、不留痕**：项目对扩展性有明确立场（扩展缝分三层：SKILL.md / `tools` 接口 / `BUILTIN_TOOLS` 源码），不记录则无法回应"为什么不用 MCP"的合理质询。

## 后果

**正面**：审计面与威胁模型不变；工具层自持，测试与基准可直接覆盖全部工具行为。

**已知缺口（如实记录）**：经 `tools` / `extra_tools` 注入的外部工具，其调用会被审计（埋点在图节点层，与工具来源无关），但**不经过**命令白名单与路径防护——注入者自担安全责任。

**未来缝**：路线图后续的统一策略门（风险分级 + 审批门做在工具层、代码强制）落成后，"外部工具强制过门"即为按需条目"MCP 接入（经网关）"的实现位置；`AuditronClawBaseTool` 预留的权限/超时字段是该机制的挂点。

## 关联代码

- `auditronclaw/core/agent.py` —— 工具装配点 `create_agent_app`
- `auditronclaw/core/tools/base.py` —— `AuditronClawBaseTool` 预留位
- `auditronclaw/core/skill_loader.py` —— SKILL.md 零代码扩展缝
