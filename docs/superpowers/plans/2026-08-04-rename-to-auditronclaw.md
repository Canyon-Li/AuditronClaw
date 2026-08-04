# 包重命名 cyberclaw → auditronclaw 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在分支 `refactor/rename-to-auditronclaw` 上，把 Python 包从 `cyberclaw` 彻底重命名为 `auditronclaw`（目录、import、类、装饰器、环境变量、CLI 命令、打包元数据），全部测试保持绿色。

**Architecture:** 一次性 big-bang 重命名，按名称的**三种大小写**拆成 4 个可独立验证的任务。关键利用点：包内部用相对 import（`.base`/`..config`），所以"重命名包目录"和"重命名内部 CamelCase 类"可以分两步做，每步后代码仍可运行、测试仍绿。已有 `pytest` 套件是回归安全网——这是重构，不写新测试（YAGNI）。

**Tech Stack:** Python 3.10+、setuptools（setup.py）、pytest、LangChain/LangGraph（不变）。Shell 用 Git Bash（POSIX）：`sed -i`、`grep -r`、`xargs -r` 均可用。

## Global Constraints

- **保留不动**：`LICENSE` 的 `Copyright (c) 2026 THOR`；`README.md` 致谢行 `fork 自 [CyberClaw](https://github.com/ttguy0707/CyberClaw)（原作者 @ttguy0707）`。
- **不做兼容垫片**：不留 `cyberclaw_tool` 别名、不回退读 `CYBERCLAW_WORKSPACE`。
- **CLI 命令**：单一正式名 `auditronclaw`，不注册短别名。
- **包内相对 import 保持相对**（`.base`、`..config`），不改成绝对 import。
- **三种大小写必须分开替换**：`cyberclaw`→`auditronclaw`（小写）、`CyberClaw`→`AuditronClaw`（驼峰）、`CYBERCLAW`→`AUDITRONCLAW`（全大写）。一次不区分大小写的替换会把 `CyberClawBaseTool` 变成 `auditronclawBaseTool`。
- 每个任务结束 `pytest` 必须全绿才能提交。

---

## File Structure

不新建源码文件。改动面：

| 路径 | 改动 |
|------|------|
| `cyberclaw/` → `auditronclaw/` | 整目录 `git mv`（保留历史） |
| `auditronclaw/core/tools/base.py` | `cyberclaw_tool`→`auditronclaw_tool`；`CyberClawBaseTool`→`AuditronClawBaseTool` |
| `auditronclaw/core/tools/builtins.py` | 同上的 import + `@` 装饰器 |
| `auditronclaw/core/tools/sandbox_tools.py` | `@cyberclaw_tool` ×4 |
| `auditronclaw/core/config.py` | `CYBERCLAW_WORKSPACE`→`AUDITRONCLAW_WORKSPACE` |
| `entry/cli.py` | import + `cyberclaw run/config` 文案 + 局部别名 |
| `entry/main.py` | import |
| `tests/*.py` | 全部 `from cyberclaw`/`import cyberclaw`/`@patch('cyberclaw...')` + env var |
| `examples/*.py` | import + env var + tempfile 前缀 |
| `setup.py` | `name`、`console_scripts`、删 `py_modules=["cli"]` |
| `.env.example` | env var 名 |
| `README.md` | 命令示例、架构表路径、目录树、env var 名 |

---

## Task 1: 重命名包目录 + 全部小写 `cyberclaw` 引用（import 与装饰器）

**Files:**
- Rename: `cyberclaw/` → `auditronclaw/`（`git mv`）
- Modify: `auditronclaw/core/tools/{base,builtins,sandbox_tools}.py`、`entry/{cli,main}.py`、`tests/*.py`、`examples/*.py`

**Interfaces:**
- Produces: 可 import 的包 `auditronclaw`；装饰器 `auditronclaw_tool`（定义于 `auditronclaw/core/tools/base.py`）。本任务后 `import auditronclaw`、`from auditronclaw.core...` 均可用。
- 注：本任务的小写替换会**顺带**改掉 `entry/cli.py` 里的 `cyberclaw run`/`cyberclaw config` 显示文案与 `cyberclaw_main`/`cyberclaw_monitor` 局部别名、以及 `examples/benchmark_lazy_loading.py` 的 `tempfile` 前缀——这些都是小写 `cyberclaw`，属于预期内改动。

- [ ] **Step 1: 建立绿色基线**

Run: `cd d:/Desktop/Code/AuditronClaw && pytest -q`
Expected: 全部通过（若因缺依赖报 collection error，先 `pip install -r requirements.txt` 再跑）。**记下通过用例数 N**，后续每个任务都应保持 ≥ N。

- [ ] **Step 2: 重命名包目录（保留 git 历史）**

Run: `cd d:/Desktop/Code/AuditronClaw && git mv cyberclaw auditronclaw`
Expected: 无输出，`cyberclaw/` 不复存在，`auditronclaw/` 取而代之。此时 `pytest` 会因 `from cyberclaw...` 全部失败——正常，下一步修复。

- [ ] **Step 3: 全仓 .py 小写 `cyberclaw` → `auditronclaw`**

Run（仅作用于包/入口/测试/示例四个目录，刻意不碰根目录 `setup.py`，留给 Task 4）：
```bash
cd d:/Desktop/Code/AuditronClaw && \
grep -rl 'cyberclaw' --include='*.py' auditronclaw entry tests examples | xargs -r sed -i 's/cyberclaw/auditronclaw/g'
```
Expected: 命中并改写 `base.py`、`builtins.py`、`sandbox_tools.py`、`entry/cli.py`、`entry/main.py`、`tests/test_*.py`、`examples/*.py`。

- [ ] **Step 4: 验证小写残留（应只剩 setup.py）**

Run: `cd d:/Desktop/Code/AuditronClaw && grep -rn 'cyberclaw' --include='*.py' .`
Expected: 仅 `setup.py` 两行（`name="cyberclaw"`、`cyberclaw=entry.cli:main`）——这两个归 Task 4。其余 .py 无任何小写 `cyberclaw`。

- [ ] **Step 5: 语法检查**

Run: `cd d:/Desktop/Code/AuditronClaw && python -m compileall auditronclaw entry tests examples -q && echo COMPILE_OK`
Expected: `COMPILE_OK`（compileall 只查语法不查 import 解析，真正的 import 校验在 Step 6）。

- [ ] **Step 6: 跑测试**

Run: `cd d:/Desktop/Code/AuditronClaw && pytest -q`
Expected: 与基线相同的 N 个用例通过、0 失败。若失败，多半是某处小写 `cyberclaw` 漏改——回到 Step 4 的 grep 排查。

- [ ] **Step 7: 提交**

```bash
cd d:/Desktop/Code/AuditronClaw && \
git add -A && \
git commit -m "refactor(rename): 移动包目录 cyberclaw -> auditronclaw 并更新全部 import"
```
Expected: 一个 commit，含目录重命名 + 一批 .py 修改。

---

## Task 2: 重命名驼峰类 `CyberClawBaseTool` → `AuditronClawBaseTool`

**Files:**
- Modify: `auditronclaw/core/tools/base.py`、`auditronclaw/core/tools/builtins.py`

**Interfaces:**
- Produces: 公开类 `AuditronClawBaseTool`（基类，供外部工具作者继承；定义于 `auditronclaw/core/tools/base.py`）。
- Consumes: Task 1 的 `auditronclaw_tool`（本任务不动）。

- [ ] **Step 1: 确认驼峰残留范围**

Run: `cd d:/Desktop/Code/AuditronClaw && grep -rn 'CyberClaw' --include='*.py' .`
Expected: 仅命中 `auditronclaw/core/tools/base.py`（class 定义 + 注释示例）与 `auditronclaw/core/tools/builtins.py`（import 行）。表面重命名阶段已清掉所有显示用 `CyberClaw`，所以只剩这个类。

- [ ] **Step 2: 替换这两个文件**

Run:
```bash
cd d:/Desktop/Code/AuditronClaw && \
sed -i 's/CyberClaw/AuditronClaw/g' auditronclaw/core/tools/base.py auditronclaw/core/tools/builtins.py
```
Expected: 两文件中所有 `CyberClawBaseTool` → `AuditronClawBaseTool`。

- [ ] **Step 3: 验证 .py 中无驼峰残留**

Run: `cd d:/Desktop/Code/AuditronClaw && grep -rn 'CyberClaw' --include='*.py' .`
Expected: 空输出。

- [ ] **Step 4: 跑测试（校验 import 解析）**

Run: `cd d:/Desktop/Code/AuditronClaw && python -c "from auditronclaw.core.tools.base import AuditronClawBaseTool, auditronclaw_tool; print('import ok')" && pytest -q`
Expected: 打印 `import ok`；pytest 仍 N 个用例通过。

- [ ] **Step 5: 提交**

```bash
cd d:/Desktop/Code/AuditronClaw && \
git add auditronclaw/core/tools/base.py auditronclaw/core/tools/builtins.py && \
git commit -m "refactor(rename): 类 CyberClawBaseTool -> AuditronClawBaseTool"
```

---

## Task 3: 重命名环境变量 `CYBERCLAW_WORKSPACE` → `AUDITRONCLAW_WORKSPACE`

**Files:**
- Modify: `auditronclaw/core/config.py`、`tests/test_lazy_loader.py`、`examples/benchmark_lazy_loading.py`、`.env.example`、`README.md`

**Interfaces:**
- Produces: 环境变量 `AUDITRONCLAW_WORKSPACE`（覆盖默认 workspace 路径；读取于 `auditronclaw/core/config.py:10`）。
- Consumes: Task 1 的包名（本任务文件路径已是 `auditronclaw/`）。

- [ ] **Step 1: 确认全大写残留范围**

Run: `cd d:/Desktop/Code/AuditronClaw && grep -rn 'CYBERCLAW' --include='*.py' --include='*.md' .`
Expected: 命中 `auditronclaw/core/config.py`、`tests/test_lazy_loader.py`、`examples/benchmark_lazy_loading.py`、`README.md`（env var 说明段）。`.env.example` 用 Step 2 的显式路径覆盖（其文件名不匹配 `--include`）。

- [ ] **Step 2: 替换全部全大写 `CYBERCLAW`**

Run:
```bash
cd d:/Desktop/Code/AuditronClaw && \
sed -i 's/CYBERCLAW/AUDITRONCLAW/g' auditronclaw/core/config.py tests/test_lazy_loader.py examples/benchmark_lazy_loading.py .env.example README.md
```
Expected: 所有 `CYBERCLAW_WORKSPACE` → `AUDITRONCLAW_WORKSPACE`。README 致谢行不含 `CYBERCLAW`（它是 `CyberClaw` 驼峰），不受影响。

- [ ] **Step 3: 验证无全大写残留**

Run: `cd d:/Desktop/Code/AuditronClaw && grep -rn 'CYBERCLAW' .`
Expected: 空输出。

- [ ] **Step 4: 重点跑 lazy_loader 测试（它直接依赖该 env var）**

Run: `cd d:/Desktop/Code/AuditronClaw && pytest tests/test_lazy_loader.py -q && pytest -q`
Expected: `test_lazy_loader.py` 全过；全量仍 N 个用例通过（config 读 `AUDITRONCLAW_WORKSPACE`、测试设 `AUDITRONCLAW_WORKSPACE`，两端一致）。

- [ ] **Step 5: 提交**

```bash
cd d:/Desktop/Code/AuditronClaw && \
git add auditronclaw/core/config.py tests/test_lazy_loader.py examples/benchmark_lazy_loading.py .env.example README.md && \
git commit -m "refactor(rename): 环境变量 CYBERCLAW_WORKSPACE -> AUDITRONCLAW_WORKSPACE"
```

---

## Task 4: 打包元数据（setup.py）+ README 命令/路径 + 真实安装冒烟测试

**Files:**
- Modify: `setup.py`、`README.md`

**Interfaces:**
- Produces: 分发包名 `auditronclaw`；CLI 命令 `auditronclaw`（`console_scripts` → `entry.cli:main`）。
- Consumes: Task 1–3 已把运行时全部切到 `auditronclaw`。

- [ ] **Step 1: 编辑 setup.py（三处）**

把 `setup.py` 改为：

```python
from setuptools import setup, find_packages

# 读取 requirements.txt
def parse_requirements(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.startswith('#')
        ]

setup(
    name="auditronclaw",
    version="1.0.0",
    packages=find_packages(),
    install_requires=parse_requirements('requirements.txt'),
    entry_points={
        "console_scripts": [
            "auditronclaw=entry.cli:main",
        ],
    },
)
```

三处变化：`name="cyberclaw"`→`"auditronclaw"`；`cyberclaw=entry.cli:main`→`auditronclaw=entry.cli:main`；**删除** `py_modules=["cli"]`（引用不存在的顶层 `cli.py`，`pip install -e .` 会报警告——潜伏 bug，顺手修）。

- [ ] **Step 2: README 小写命令/路径替换**

Run（小写 `cyberclaw`：命令示例 `cyberclaw run/config/monitor`、架构表路径 `cyberclaw/core/...`、目录树 `├── cyberclaw/`。致谢行是驼峰 `CyberClaw`，不会被小写匹配）：
```bash
cd d:/Desktop/Code/AuditronClaw && \
sed -i 's/cyberclaw/auditronclaw/g' README.md
```

- [ ] **Step 3: 验证 README 致谢行完好**

Run: `cd d:/Desktop/Code/AuditronClaw && grep -n 'ttguy0707/CyberClaw' README.md`
Expected: 命中致谢行，仍为 `fork 自 [CyberClaw](https://github.com/ttguy0707/CyberClaw)（原作者 @ttguy0707）`。若被误改，手动还原该行。

- [ ] **Step 4: 全仓最终残留扫描**

Run: `cd d:/Desktop/Code/AuditronClaw && grep -rEn 'cyberclaw|CyberClaw|CYBERCLAW' .`
Expected: **仅** `README.md` 致谢行（含 `CyberClaw`）匹配。`LICENSE` 不含 cyberclaw 字样（只有 THOR / Canyon-Li），不匹配。其余全部清零。

- [ ] **Step 5: 真实安装 + CLI 冒烟**

Run:
```bash
cd d:/Desktop/Code/AuditronClaw && \
pip install -e . -q && \
auditronclaw --help && \
echo "--- old command should be gone ---" && \
(cyberclaw --help 2>&1 || true)
```
Expected:
- `pip install -e .` 无 `py_modules` 警告、无报错。
- `auditronclaw --help` 打印 typer 帮助（含 `config`/`run`/`monitor` 子命令）。
- `cyberclaw --help` 报 "command not found"（旧入口已不存在）。

- [ ] **Step 6: 最终全量测试**

Run: `cd d:/Desktop/Code/AuditronClaw && pytest -q`
Expected: N 个用例全过，0 失败。

- [ ] **Step 7: 提交**

```bash
cd d:/Desktop/Code/AuditronClaw && \
git add setup.py README.md && \
git commit -m "refactor(rename): setup.py 包名/CLI 命令 -> auditronclaw，更新 README，移除失效 py_modules"
```

---

## 完成判据

- 4 个 commit 全部落地于 `refactor/rename-to-auditronclaw`。
- `grep -rEn 'cyberclaw|CyberClaw|CYBERCLAW' .` 仅剩 README 致谢行。
- `auditronclaw --help` 可用；`cyberclaw` 命令不复存在。
- `pytest` 全绿，用例数 ≥ 基线 N。
- 合并回 `main` 前由人工 review。

## 回滚

`git checkout main` 即可（main 未被触碰）；分支上 `git reset --hard origin/refactor/rename-to-auditronclaw` 或删分支重来。
