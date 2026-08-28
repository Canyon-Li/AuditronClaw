"""tasks.json 原子落盘。

待办队列文件此前有四处各自裸写（追加、删除、修改、心跳触发后重写），
任何一处写到一半崩溃或断电，磁盘上就是半截 JSON——tasks.json 是定时
任务队列的唯一样本，半截即整体失明（心跳、排程、事务台都读它）。
本文件钉住收敛后的两条底线：

1. 全部写入只经 _write_tasks 一处：同目录 tmp 写入 → flush+fsync →
   os.replace。tmp 写中途崩溃，正式文件保持旧内容且可解析。
2. 生产代码里对 TASKS_FILE 的写模式 open() 只允许出现在 _write_tasks
   内部——新增裸写点在这里红掉。

明确不做"先写回再触发"（mark-then-run）：那会把崩溃窗口换成漏执行，
漏一天日报即存活信号丢失，比低概率重复触发伤；重复触发维持
"低概率，自用可容忍"挂账，靠任务幂等消化。
"""
import ast
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import auditronclaw.core.tools.builtins as builtins_mod

REPO_ROOT = Path(__file__).resolve().parent.parent


def _is_tasks_write_open(node):
    """open() 首实参引用 TASKS_FILE 且模式含写位(w/a/x/+)——读模式不算。"""
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "open"
            and node.args):
        return False
    if not any(isinstance(n, ast.Name) and n.id == "TASKS_FILE"
               for n in ast.walk(node.args[0])):
        return False
    mode_node = node.args[1] if len(node.args) > 1 else None
    for kw in node.keywords:
        if kw.arg == "mode":
            mode_node = kw.value
    if mode_node is None:
        return False  # 未给 mode 即默认读
    return (isinstance(mode_node, ast.Constant)
            and isinstance(mode_node.value, str)
            and any(c in mode_node.value for c in "wax+"))


class TestWriteEntryConvergence(unittest.TestCase):
    """写入口收敛 4→1:生产代码对 TASKS_FILE 的写打开只许在 _write_tasks 内。"""

    def _write_opens(self, tree):
        return [n for n in ast.walk(tree) if _is_tasks_write_open(n)]

    def test_single_write_open_lives_inside_write_tasks(self):
        """全仓只此一处写打开,且嵌在 _write_tasks 函数体内。"""
        hits = []  # (相对路径, 行号)
        # 生产代码全量扫描:包本体、入口、示例与基准——tests 自建临时文件,
        # 不经 TASKS_FILE 常量,不在收敛范围内
        for py in sorted(sum((list(REPO_ROOT.glob(f"{d}/**/*.py"))
                              for d in ("auditronclaw", "entry",
                                        "benchmarks", "examples")), [])):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            hits += [(f"{py.relative_to(REPO_ROOT)}:{n.lineno}")
                     for n in self._write_opens(tree)]

        self.assertEqual(
            len(hits), 1,
            f"对 TASKS_FILE 的写打开必须唯一(当前: {hits});"
            "新增写点一律改调 _write_tasks")

        builtins_py = REPO_ROOT / "auditronclaw" / "core" / "tools" / "builtins.py"
        tree = ast.parse(builtins_py.read_text(encoding="utf-8"))
        writer_bodies = [
            f for f in ast.walk(tree)
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
            and f.name == "_write_tasks"
        ]
        self.assertTrue(writer_bodies, "builtins.py 必须定义 _write_tasks")
        self.assertTrue(
            any(self._write_opens(f) for f in writer_bodies),
            "唯一写打开必须位于 _write_tasks 函数体内")


class TestAtomicTasksWrite(unittest.TestCase):
    """_write_tasks 本体:崩溃安全、成功路径、锁约定。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tasks_path = os.path.join(self.tmp_dir, "tasks.json")
        self._orig_tasks_file = builtins_mod.TASKS_FILE
        builtins_mod.TASKS_FILE = self.tasks_path

    def tearDown(self):
        builtins_mod.TASKS_FILE = self._orig_tasks_file
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _old_tasks(self):
        return [{
            "id": "old1",
            "target_time": "2026-08-30 09:00:00",
            "description": "旧任务",
            "repeat": None,
            "repeat_count": None,
        }]

    def _new_tasks(self):
        return [{
            "id": "new1",
            "target_time": "2026-08-31 09:00:00",
            "description": "新任务",
            "repeat": "daily",
            "repeat_count": None,
        }]

    def _write_old_file(self):
        with open(self.tasks_path, "w", encoding="utf-8") as f:
            json.dump(self._old_tasks(), f, ensure_ascii=False, indent=2)

    def test_crash_mid_write_keeps_old_content_parseable(self):
        """tmp 写中途崩溃:正式文件保持旧内容且可解析——原子替换的意义所在。"""
        self._write_old_file()

        def half_dump(obj, fh, **kw):
            fh.write("[")  # 半截 JSON 落进 tmp,正式文件不该被碰
            raise OSError("模拟写入中途崩溃/断电")

        with patch.object(builtins_mod.json, "dump", half_dump):
            with self.assertRaises(OSError):
                builtins_mod._write_tasks(self._new_tasks())

        with open(self.tasks_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), self._old_tasks(),
                             "崩溃后正式文件必须原封保留旧队列且可解析")

    def test_successful_write_replaces_content(self):
        """成功路径:新内容整体替换,中文不转义、缩进保留,无 tmp 残留。"""
        self._write_old_file()

        builtins_mod._write_tasks(self._new_tasks())

        with open(self.tasks_path, encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(json.loads(content), self._new_tasks())
        self.assertIn("新任务", content, "ensure_ascii=False:中文不得转成 \\uXXXX")
        self.assertIn('\n  {', content, "indent=2 的两空格缩进应保留")
        self.assertFalse(os.path.exists(self.tasks_path + ".tmp"),
                         "成功替换后临时文件应被消费掉")

    def test_write_does_not_reacquire_tasks_lock(self):
        """锁约定:调用方持锁调用必须能完成——_write_tasks 不自取非重入锁。"""
        done = threading.Event()

        def run():
            builtins_mod._write_tasks(self._new_tasks())
            done.set()

        worker = threading.Thread(target=run)
        with builtins_mod.tasks_lock:  # 模拟所有真实调用方:先持锁再写
            worker.start()
            worker.join(timeout=3)
            self.assertTrue(
                done.is_set(),
                "调用方持有 tasks_lock 时 _write_tasks 3 秒内未完成——"
                "它在自取锁,非重入 threading.Lock 即死锁")
        worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
