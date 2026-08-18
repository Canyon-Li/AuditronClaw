# AuditronClaw 变更日志

## [1.1.0] - 2026-08-18

从 [CyberClaw](https://github.com/ttguy0707/CyberClaw) fork 后的首个正式版本：完成安全审计与加固，建立双维评测管线。

### 🔒 安全加固

- **calculator eval 注入修复**：`eval()` 求值替换为 AST 节点白名单求值器，仅放行算术节点。原实现可通过 `__class__.__bases__` 属性链逃逸获得 `os.system`，等于任意代码执行
- **shell 命令校验重构**：五条正则黑名单替换为 shlex 结构化命令白名单（`execute_office_shell`）。原实现可被环境变量展开（`cat $HOME/.ssh/id_rsa`）、引号包裹的内联解释器（`python -c ...`）等绕过；白名单外的二进制一律拒绝，可经 `AUDITRONCLAW_ALLOWED_COMMANDS` 显式扩展
- **技能 run 命令面闭合**：技能 `run` 模式的命令与手动 shell 走同一校验器，恶意 SKILL.md 无法借技能通道代理执行越权命令
- **会话隔离**：`--thread` 参数化（对话历史/审计日志按会话分文件），长期画像从全局单文件改为 `memory/profiles/<thread_id>.md` 按会话存储并保留写入留痕（行级 diff 记审计日志），封死"画像污染 → 跨会话持久化注入"链

### 📊 双维评测管线

- **注入拦截基准**（`benchmarks/run_injection_bench.py`）：50 条恶意用例覆盖四个入口面（恶意 SKILL.md / 文件内容注入 / 污染画像 / 直接越狱），双层判定（提示面拦截率 77.5% × 危害落地率 2.0%），每用例独立 workspace 隔离
- **Golden 能力基准**（`benchmarks/run_golden_eval.py`）：37 条正常任务覆盖六个能力面，判定器全确定性断言（无 LLM-as-judge），副作用任务双锚断言（工具调用 + 落盘终态）。任务达成率 83.8%，安全假阳性（over_refusal）0 条
- **共享流水线**（`benchmarks/bench_pipeline.py`）：reload 链隔离 + astream 轨迹收集 + JSONL 落盘，两套基准零重复代码

### 🏗️ 基础设施

- **CI**：GitHub Actions 全量测试（62 用例含红队）+ 每周双基准冒烟（schedule/手动触发，日常 push 不消耗 API 配额）
- **修复打包缺陷**：补 `auditronclaw/core/tools/__init__.py`——此前 `find_packages()` 不收录该子包，pip 安装后 CLI 启动即 `ModuleNotFoundError`（源码目录运行不触发，故长期未被发现）

### ✅ 上游回流

- [CyberClaw#18](https://github.com/ttguy0707/CyberClaw/pull/18)：calculator eval RCE 修复（已合并）
- [CyberClaw#19](https://github.com/ttguy0707/CyberClaw/pull/19)：shell 白名单重构（已合并）

---

## [1.0.0] - 2026-07-31

上游 CyberClaw 基线版本。包含：LangGraph agent 核心、两段式技能调用（help → run）、懒加载技能加载器（启动仅扫元数据、首调才加载全文、LRU 缓存、支持热更新）、双水位记忆、心跳任务引擎、JSONL 审计日志与监控终端。
