# ADR-002: 域包模板与登记面收窄

日期：2026-08-31 · 状态：已接受

## 背景

给 AuditronClaw 加一个域功能（邮箱事务台、飞书推送之后的下一个），今天要手改基座文件：工具实现平铺在 core/tools/，静态分级名册手维护在 classifier.py，装配清单在 builtins.py。忘登记对操作员是烦（每次问人审批），对心跳是静默不可用且没有报错指路。三个已有域三种交付形状（数据模型内联在基座装配文件 / 无数据形状 / 工具内直写审计），新人无法预测新工具放哪、照谁抄——分歧被一个又一个新域复制。

本 ADR 定域包模板：让"加一个域功能要动基座几处"收窄且如实分档——静态分级工具零基座编辑，条件分级工具一处（core 名册），两者都加装配点一行。

## 决策

### 域包模板（槽位唯一表）

目录：`auditronclaw/domains/<name>/`，物理边界画在文件系统里。

| 槽位 | 唯一答案 | 负空间（不许有） |
|---|---|---|
| 数据形状 | `models.py`，仅 pydantic | 任何 I/O；import 本域 tool.py |
| 工具实现 | `tool.py`：业务逻辑 + 持久化 + LLM 薄壳 | 网络库 import（出站走 egress 登记）；手写审计回执（回执走 Receipt→AuditReceiptHook 单源） |
| 接线 | `register()`（tool.py 尾部或 `__init__.py`） | 副作用；读环境 |
| 条件分级工具的风险声明 | **不声明**——判定属基座（域名门），留 core 名册 | 绑定域工具名出现在 risk 映射里 |

判定标准：新人只读本表、不读任何域代码，能预测每个文件里有什么、没有什么。文件数量是裁量，槽位是纪律。

依赖方向：domains → core 单向；core 不 import 任何域；只有装配点 import 域（显式 import + 调用 register()，不做自动发现——装配点要可读，不要魔法）。

### 登记契约（register()）

```python
@dataclass(frozen=True)
class DomainRegistration:
    tools: tuple[BaseTool, ...]
    risk: Mapping[str, RiskCategory]       # 仅静态分级词汇（read/write/delete）
    egress: tuple[EgressChannel, ...] = () # 无出站留空
```

- 类型放 core（`auditronclaw/core/domain.py`）：依赖方向决定
- 每域恰好一个 register() 返回本类型；tools 为空仅限测试夹具域
- frozen——与 WorkspaceConfig 同一风格，装配期快照，构造即固化

### 澄清：名册不是"按名分级"

副作用分级的轴是操作的副作用，不是工具名（CONTEXT.md「副作用分级」）。名册以工具名为键只是实现形态——键指向的是该工具操作的副作用级别。据此分两类：静态分级工具（级别不随运行时状态变化，纯读/写/删）由域在 register().risk 自报；条件分级工具（级别依赖运行时状态）域不自报，一律登记在 core 名册。

## 裁定记录

1. **条件分级工具一律留 core 名册，域不自报**。绑定域工具（read_recent_emails / send_feishu_summary）的分级是条件式：名单内 read、名单外 domain_extend 带 target——级别取决于域名白名单当刻内容，frozen 声明式快照捕获不了运行时状态。分界轴是"级别是否依赖运行时状态"，绑定域工具是第一个实例。
2. **声明式 sentinel 方案否决**（现时）。曾评估：域声明 BOUND_DOMAIN("…") 式 sentinel，分级器运行期解析两分支，可保名册单源。否决理由：union 类型复杂化契约；把安全关键的绑定元数据挪进域 PR 的评审面。重启条件：绑定域工具多到一处 core 名册成为负担时重新评估。
3. **core 名册的域名以字符串字面量写死**。core 不 import 域模块（否则依赖方向反向）；字面量与域 register().egress 声明域名的一致性由 meta-test 把守，两处事实源不漂移。
4. **名册装配期合并注入**。合并名册 := core 静态名册 ∪ 各域自报；装配点构造 frozen 名册注入审批门，分级器 builtin 判定路径带册、不再直读模块 frozenset。跨来源任何同名（不论级别）装配期即 RuntimeError，级别只进报错信息。

## 槽位 × 首个实测者

| 槽位 | 首个实测者 | 状态 |
|---|---|---|
| tool.py / register() / 回执走 hooks / egress 声明 | feishu 域迁移 | 已排期，未实测 |
| risk 静态自报 | 测试夹具域 + 首个生产静态域 | 夹具先行，生产首用待下一个新域 |
| models.py | 首个有数据形状的域 | 纸面，未实测 |
| 持久化（照 _write_tasks 形状） | 首个新持久化域 | 纸面，未实测；不预抽象 |

**槽位承诺**：下一个新域（无论是什么）必须走模板并补齐 risk 生产首用；不造 stub 域凑验证。

## 后果

**正面**：加静态分级工具零基座编辑；域包一次 PR 交付完整能力；分歧收敛为一（新人照槽位表开工）。

**代价与如实分档**：条件分级工具仍要动一处基座（core 名册字面量）——成本如实可见，不假装免费；两者都加装配点一行（装配点本职，不计基座编辑）。

**改造护栏**：语义不变由改造前基线夹具证明（`tests/baseline/`：装配工具集合 + 分级快照 + 纸面追踪三件），改造后对照同一基线，不靠 git 考古与记忆。基线覆盖边界如实记录：默认装配（内置工厂产物，shell 在内）；技能与外接工具设计上 unclassified（fail-closed 默认必批），不拍分级快照，集合等值检查在改造收口时连同两者一起覆盖。

## 关联代码

- `auditronclaw/core/domain.py` —— DomainRegistration 契约
- `auditronclaw/domains/` —— 域包目录（骨架）
- `tests/baseline/` —— 改造前基线三件
- `auditronclaw/core/approval/classifier.py` —— core 名册现状（静态 12 名：纯读 6 / 写 5 / 删 1；绑定域 2 名）
