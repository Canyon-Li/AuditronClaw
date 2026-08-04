# 设计文档：Python 包重命名 cyberclaw → auditronclaw

- **日期**：2026-08-04
- **状态**：设计已批准，待实施
- **分支**：`refactor/rename-to-auditronclaw`
- **前置**：表面文案重命名已完成并提交到 `main`（commit `676c136`）

---

## 1. 背景

AuditronClaw 是 `ttguy0707/CyberClaw` 的 fork。表面文案（系统提示词、CLI 横幅、README、注释等）已统一改为 AuditronClaw，但**内部 Python 包名仍是 `cyberclaw`**——这是最后一处上游身份残留。本 spec 把包彻底重命名为 `auditronclaw`。

## 2. 目标 / 非目标

**目标**
- 将 Python 包目录、对外 API 符号、环境变量、CLI 命令全部从 `cyberclaw` 改为 `auditronclaw`。
- 保留被重命名目录的 git 历史。
- 全部测试通过。

**非目标（YAGNI）**
- 不做向后兼容垫片：不留 `cyberclaw_tool` 别名、不读 `CYBERCLAW_WORKSPACE` 旧环境变量。本项目尚无外部用户，干净切断。
- 不动任何合规溯源内容（LICENSE 原版权、README 致谢行）。
- 不改变运行时行为、日志文件名（`local_geek_master.jsonl`）、thread_id、workspace 目录名。

## 3. 已定决策

| 决策点 | 选择 |
|--------|------|
| 执行方式 | **一次性 big-bang**，单分支单连贯变更，`pytest` + `py_compile` 做验收门 |
| 重命名范围 | **彻底**：目录 / import / 类 / 装饰器 / 环境变量 / CLI 命令全改 |
| CLI 命令名 | **`auditronclaw`**（单一正式名，不注册短别名） |
| 向后兼容 | **无** |

## 4. 改动地图

### 4.1 目录重命名（保留历史）
```
git mv cyberclaw auditronclaw
```
包内相对 import（`.base`、`..config` 等）不受影响，无需改动。

### 4.2 三种大小写的全局精确替换
**必须按三种 case 分别做精确替换**——一次不区分大小写的替换会把 `CyberClawBaseTool` 变成 `auditronclawBaseTool`（大小写错乱）。

| 变体 | 从 | 到 |
|------|----|----|
| 全小写 | `cyberclaw` | `auditronclaw` |
| 驼峰 | `CyberClaw` | `AuditronClaw` |
| 全大写 | `CYBERCLAW` | `AUDITRONCLAW` |

受影响的符号与位置：

- **类** `CyberClawBaseTool` → `AuditronClawBaseTool`
  - 定义：`cyberclaw/core/tools/base.py:12`（重命名后为 `auditronclaw/core/tools/base.py`）
  - import：`builtins.py:2`
  - 注释示例：`base.py:48`
- **装饰器别名** `cyberclaw_tool` → `auditronclaw_tool`
  - 定义：`base.py:9`
  - 全部 `@cyberclaw_tool` 用法：`builtins.py`、`sandbox_tools.py`
- **绝对 import** `from cyberclaw...` / `import cyberclaw` → `from auditronclaw...` / `import auditronclaw`
  - `entry/cli.py`、`entry/main.py`
  - `tests/` 下所有测试
  - `examples/basic_usage.py`、`examples/benchmark_lazy_loading.py`
  - 包内若有绝对 import 同样处理（默认是相对 import，预计无）
- **环境变量** `CYBERCLAW_WORKSPACE` → `AUDITRONCLAW_WORKSPACE`
  - `auditronclaw/core/config.py:10`
  - `tests/test_lazy_loader.py`
  - `examples/benchmark_lazy_loading.py`
  - `.env.example`、README 配置说明段

### 4.3 setup.py
- `name="cyberclaw"` → `name="auditronclaw"`
- `console_scripts`：`cyberclaw=entry.cli:main` → `auditronclaw=entry.cli:main`
- **顺手修潜伏 bug**：删除 `py_modules=["cli"]`——它引用不存在的顶层 `cli.py`（实际在 `entry/cli.py`），`pip install -e .` 会报警告。

### 4.4 CLI 显示文案里的命令示例
> 此前表面重命名时刻意保留了这些（因为命令实际还叫 cyberclaw）；命令真改名后，文案跟着改。

- `entry/cli.py`：`cyberclaw run` / `cyberclaw config` 提示文字 → `auditronclaw run` / `auditronclaw config`（约 153、161 行）
- README：所有命令示例 `cyberclaw run/config/monitor` → `auditronclaw ...`；架构表路径 `cyberclaw/core/...` → `auditronclaw/core/...`；目录树 `├── cyberclaw/` → `├── auditronclaw/`

### 4.5 纯装饰性清理
- `examples/benchmark_lazy_loading.py`：`tempfile.mkdtemp(prefix="cyberclaw_bench_")` → `"auditronclaw_bench_"`
- `entry/cli.py`：局部别名 `import entry.main as cyberclaw_main` → `as auditronclaw_main`，`as cyberclaw_monitor` → `as auditronclaw_monitor`（非必须，为一致性顺手改）

## 5. 例外（必须保留不动）

| 位置 | 内容 | 原因 |
|------|------|------|
| `LICENSE` | `Copyright (c) 2026 THOR` | MIT 合规义务，原版权声明不可删 |
| `README.md` 致谢行 | `fork 自 [CyberClaw](https://github.com/ttguy0707/CyberClaw)（原作者 @ttguy0707）` | 指向上游的溯源信息 |

> 替换时需绕开 README 致谢行（建议：全局替换后单独还原该行，与表面重命名阶段同款手法）。

## 6. 验收

1. **语法**：`python -m compileall auditronclaw entry tests examples`（重命名后目录已是 `auditronclaw`；或对改动文件逐一 `py_compile`）。
2. **测试**：`pytest` 全绿。测试大量 `from cyberclaw...`，在替换范围内一并更新。
3. **真实安装**：`pip install -e .` 后执行 `auditronclaw config`，确认控制台入口在新名下可用（旧名 `cyberclaw` 应不再存在）。
4. **残留扫描**：`grep -rEn "cyberclaw|CyberClaw|CYBERCLAW" .` 最终**只剩 README 致谢行**（其内的 `[CyberClaw](...ttguy0707/CyberClaw)` 是上游溯源，故保留）。`LICENSE` 不含 `cyberclaw` 字样（只有 THOR / Canyon-Li），不会匹配。

## 7. 回滚

整个重命名在独立分支上进行，`main` 不受影响。失败时 `git checkout main` 即可；分支上 `git reset --hard` 可整体丢弃。

## 8. 范围外

- `.gitignore` / `.vscode/settings.json` 的 housekeeping 改动（留给仓库所有者单独决定）。
- `魔改潜力评估.md`（所有者个人分析文档）。
- 对 agent 行为、技能系统、心跳逻辑的任何功能性改动。
