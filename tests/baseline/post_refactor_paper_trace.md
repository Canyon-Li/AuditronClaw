# 改造后纸面执行追踪（登记面收窄与域包模板 · 收口对照）

同一假想域（notes：纯读 list_notes + 写 save_notes）在改造后的架构上纸面重走，与改造前基线（隔壁 `pre_refactor_paper_trace.md`）逐项对照。数字全部实测、出处随行标注；采集日 2026-08-31，收口分支 feat/assembly-closure-and-snapshot。

## 假想域 notes（静态分级工具两枚）

| 度量 | 改造前 | 改造后实测 | 改造后出处 |
|---|---|---|---|
| 基座文件编辑数（静态分级工具） | 2 | **0** | 分级由域 `register().risk` 自报、装配期合并（roster.build_static_risk）；工具不经 core 工厂（domains/notes/tool.py），builtins/classifier 零改动 |
| 基座文件编辑数（条件分级=绑定域工具） | 3 | **1**（名单入口另分档，见下） | classifier.py 绑定域册加一行字符串字面量；装配点一行接线属装配点本职，不计基座 |
| 为知道"放哪、照谁"要读的代码 | core/tools 平铺 9 文件 1970 行 | **ADR-002（77 行）+ domains/__init__.py 约定注释（9 行）= 86 行**（wc -l 实测） | 分工表 + 负空间规则即全部交付知识；feishu 可作样例但不读也能开工 |
| 分歧位点数 | 3（内联模型 / 无模型 / 手写回执） | **1**（模板唯一形状） | 新域交付形状唯一：tool.py + register() + 回执走 Receipt→hooks 单源 |

## 条件分级的名单入口如实分档

classifier 字面量之外，绑定域名进白名单有三条路：默认名单（domain_gate.py，feishu/mail 现状，再 +1 基座编辑，域模块 import 期 assert 把关）；部署侧环境变量 AUDITRONCLAW_ALLOWED_DOMAINS（0）；运行期审批规则铸入（0，"永久允许"当轮生效）。即条件分级工具基座编辑 1（默认名单路线为 2），路线随域交付 PR 评审定并如实计入。

## 收口新增的机器把守

- 接线表单一来源：agent.py `_DOMAIN_REGISTRARS`（每域一条，feishu 在表）；工具装配位两种（原位插回=存量迁移域保基线序 / 表追加=新域），登记与装配检查一套
- AST 扫描：core 零域 import（装配点唯一例外且验明非空转），tests/test_core_domain_import_scan.py
- 装配集与分级快照逐项 == 票 01 基线（tests/test_pre_refactor_baseline.py，21 条含绑定域双分支）；域工具与旧域工具全部带 approval_gate 元数据（TestAssemblyPointWrapping 扩展）

## 备注

旧域（mail / desk / tasks）保留原形状原地（spec Out of Scope：下次被碰到时迁移）——desk_tool.py 仍有 2 处工具内直写审计、builtins.py 仍内联 ScheduledTask 模型。"3 → 1"指新域交付形状唯一，不是旧域形状已消失。
