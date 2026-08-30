"""任务队列条目模型化（04 票 B）。

条目形状此前散在约 8 处暗示（_append_task 的 dict 字面量、docstring、
心跳的键读取、两套测试 fixture、golden_cases.yaml 的 setup 字面量……），
无任何校验：repeat="sometimes" 不会在任何一层被拒绝，只是触发一次后
无声消失；heartbeat 的裸 except 把坏值静默吞掉。本文件钉住模型化后的
底线：

1. ScheduledTask 是形状唯一声明：5 键、repeat 收紧四值 Literal、
   target_time 严格格式；旧形状条目（golden_cases.yaml 的 setup 字面量
   即现成样本）原样通过。
2. 文件边界统一 model_validate / model_dump：写入走 _write_tasks。
3. 校验失败记审计回执再跳过（替换裸 except）：遗留坏 repeat 值从
   "首触后静默消失"变为"校验拒绝 + 回执 + 跳过"（永不触发但可见）。
4. 加字段演示：新字段只动模型 1 处 + 消费点，生产链路对未声明字段透明。
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from typing import Literal, get_args, get_origin
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from pydantic import ValidationError

from auditronclaw.core.tools.builtins import ScheduledTask, _append_task, schedule_task

# golden_cases.yaml（gold_task_003 setup）的条目字面量：old-shape 现成样本
GOLDEN_OLD_SHAPE_ENTRY = {
    "id": "t1001",
    "target_time": "2099-01-01 09:00:00",
    "description": "旧提醒:喝水",
    "repeat": None,
    "repeat_count": None,
}

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _repeat_values():
    """从模型声明提取 repeat 的 Literal 取值（唯一事实源，不做第二份清单）。"""
    annotation = ScheduledTask.model_fields["repeat"].annotation
    for arg in get_args(annotation) or (annotation,):
        if get_origin(arg) is Literal:
            return get_args(arg)
    raise AssertionError(f"repeat 注解必须是 Literal[...],当前是 {annotation!r}")


class TestScheduledTaskModel(unittest.TestCase):
    """ScheduledTask：形状唯一声明（5 键 + 四值 Literal + 时间格式）。"""

    def test_old_shape_entry_round_trips_unchanged(self):
        """old-shape fixture 原样通过并整体往返（键与值逐项一致）"""
        task = ScheduledTask.model_validate(GOLDEN_OLD_SHAPE_ENTRY)
        self.assertEqual(task.model_dump(), GOLDEN_OLD_SHAPE_ENTRY)

    def test_repeat_literal_is_exactly_four_values(self):
        """repeat 收紧为四值 Literal——monthly 在册（代码本就支持，补进声明）"""
        self.assertEqual(set(_repeat_values()),
                         {"hourly", "daily", "weekly", "monthly"})

    def test_each_literal_value_validates(self):
        for value in _repeat_values():
            with self.subTest(repeat=value):
                entry = {**GOLDEN_OLD_SHAPE_ENTRY, "repeat": value}
                self.assertEqual(ScheduledTask.model_validate(entry).repeat, value)

    def test_unknown_repeat_value_rejected(self):
        """repeat="sometimes"（拼错值）在模型层即被拒,不再无声消失"""
        with self.assertRaises(ValidationError):
            ScheduledTask.model_validate({**GOLDEN_OLD_SHAPE_ENTRY,
                                          "repeat": "sometimes"})

    def test_missing_repeat_keys_default_to_none(self):
        """旧数据缺键：模型默认值兜底（不靠各处 .get() 防御）"""
        entry = {"id": "t9", "target_time": "2099-01-01 09:00:00",
                 "description": "缺循环键的旧条目"}
        task = ScheduledTask.model_validate(entry)
        self.assertIsNone(task.repeat)
        self.assertIsNone(task.repeat_count)

    def test_bad_target_time_format_rejected(self):
        """target_time 格式在模型层声明——入口校验与心跳触发解析同一口径"""
        with self.assertRaises(ValidationError):
            ScheduledTask.model_validate(
                {**GOLDEN_OLD_SHAPE_ENTRY, "target_time": "2099-01-01 09:00"})

    def test_undeclared_key_survives_dump(self):
        """未声明键随模型透传不丢：心跳续期整文件重写不毁未来字段
        （加字段演示测试的前提）"""
        entry = {**GOLDEN_OLD_SHAPE_ENTRY, "priority": 3}
        self.assertEqual(ScheduledTask.model_validate(entry).model_dump(), entry)

    def test_schedule_docstring_documents_all_repeat_values(self):
        """docstring 与模型同步：每个 Literal 值都在参数说明里
        （monthly 曾是"代码支持、文档缺失"）"""
        for value in _repeat_values():
            self.assertIn(value, schedule_task.description)


class TestAppendTaskModelBoundary(unittest.TestCase):
    """_append_task：内部构造模型，落盘条目形状由 model_dump 决定。"""

    def setUp(self):
        fd, self.tasks_path = tempfile.mkstemp(suffix=".json")
        # Windows:写路径测试不得持有句柄,别挡 _write_tasks 的 os.replace
        os.close(fd)
        os.unlink(self.tasks_path)  # 从"文件不存在"开始:被拒路径断言条目不得落盘
        import auditronclaw.core.tools.builtins as builtins_mod
        self._builtins = builtins_mod
        self._orig = builtins_mod.TASKS_FILE
        builtins_mod.TASKS_FILE = self.tasks_path

    def tearDown(self):
        self._builtins.TASKS_FILE = self._orig
        for p in (self.tasks_path, self.tasks_path + ".tmp"):
            if os.path.exists(p):
                os.unlink(p)

    def test_appended_entry_has_declared_five_key_shape(self):
        """追加条目恰好 5 键、按声明序输出——形状只由模型一处声明"""
        _append_task("2030-01-01 09:00:00", "模型边界任务", repeat="monthly")

        with open(self.tasks_path, encoding="utf-8") as f:
            (entry,) = json.load(f)
        self.assertEqual(list(entry.keys()),
                         ["id", "target_time", "description", "repeat",
                          "repeat_count"])
        self.assertEqual(entry["description"], "模型边界任务")
        self.assertEqual(entry["repeat"], "monthly")
        self.assertRegex(entry["id"], r"^[0-9a-f]{8}$")

    def test_schedule_task_rejects_unknown_repeat_without_touching_file(self):
        """repeat="sometimes"：结构化拒绝,话术列出四个合法值,条目不落盘"""
        future = (datetime.now() + timedelta(hours=2)).strftime(TIME_FORMAT)

        result = schedule_task.invoke({"target_time": future,
                                       "description": "拼错循环值",
                                       "repeat": "sometimes"})

        self.assertIn("设定失败", result)
        for value in _repeat_values():
            self.assertIn(value, result)
        self.assertFalse(os.path.exists(self.tasks_path),
                         "被拒条目不得写进任务队列")


class _PacemakerHarness(unittest.TestCase):
    """三处 TASKS_FILE 引用钉进同一临时文件、真跑 pacemaker_loop 的公共底座。"""

    def setUp(self):
        import auditronclaw.core.config
        import auditronclaw.core.heartbeat
        import auditronclaw.core.tools.builtins

        fd, self.tasks_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)  # Windows:立即关句柄,别挡写路径的 os.replace
        self._modules = (auditronclaw.core.config,
                         auditronclaw.core.heartbeat,
                         auditronclaw.core.tools.builtins)
        self._orig = [m.TASKS_FILE for m in self._modules]
        for m in self._modules:
            m.TASKS_FILE = self.tasks_path

    def tearDown(self):
        for m, orig in zip(self._modules, self._orig):
            m.TASKS_FILE = orig
        for p in (self.tasks_path, self.tasks_path + ".tmp"):
            if os.path.exists(p):
                os.unlink(p)

    def _write_tasks_file(self, tasks):
        with open(self.tasks_path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)

    def _read_tasks_file(self):
        with open(self.tasks_path, encoding="utf-8") as f:
            return json.load(f)

    def _run_pacemaker(self, seconds=0.3, interval=0.05):
        """跑起搏器若干秒后取消,返回任务队列（已触发的消息都在里面）。"""
        from auditronclaw.core.heartbeat import pacemaker_loop

        queue = asyncio.Queue()

        async def drill():
            worker = asyncio.create_task(
                pacemaker_loop(task_queue=queue, check_interval=interval))
            await asyncio.sleep(seconds)
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        asyncio.run(drill())
        return queue

    @staticmethod
    def _drain(queue):
        messages = []
        while not queue.empty():
            messages.append(queue.get_nowait())
        return messages


class TestHeartbeatValidationReceipt(_PacemakerHarness):
    """校验失败记审计回执再跳过——替换裸 except 的静默吞掉（验收钉子）。"""

    def test_legacy_bad_repeat_rejected_with_receipt_not_triggered(self):
        """遗留 repeat="sometimes"：校验拒绝 + 回执 + 跳过（永不触发但可见）。

        模型化前：坏值任务首次触发时照发提醒,续期分支认不出该值,
        整文件重写后被静默丢弃;模型化后：不触发、移出队列,
        拒绝留 system 级审计回执。
        """
        future_time = (datetime.now() + timedelta(hours=1)).strftime(TIME_FORMAT)
        self._write_tasks_file([
            {"id": "bad01", "target_time": "2020-01-01 09:00:00",
             "description": "拼错循环值的旧任务", "repeat": "sometimes",
             "repeat_count": None},
            {"id": "keep01", "target_time": future_time,
             "description": "合法未来任务", "repeat": None, "repeat_count": None},
        ])

        with patch("auditronclaw.core.tools.builtins.audit_logger") as mock_logger:
            queue = self._run_pacemaker()

        # 永不触发：队列零消息（旧行为会先把提醒发给会话,再无声消失）
        self.assertEqual(len(self._drain(queue)), 0)

        # 回执恰好一条：system 级 system_action,点名拒绝与条目标识
        (receipt,) = mock_logger.log_event.call_args_list
        self.assertEqual(receipt.kwargs["thread_id"], "system")
        self.assertEqual(receipt.kwargs["event"], "system_action")
        self.assertIn("校验拒绝", receipt.kwargs["content"])
        self.assertIn("bad01", receipt.kwargs["content"])

        # 跳过：坏条目移出队列,合法未来任务原样保留
        tasks = self._read_tasks_file()
        self.assertEqual([t["id"] for t in tasks], ["keep01"])
        self.assertEqual(tasks[0]["target_time"], future_time)

    def test_future_task_with_bad_repeat_rejected_at_read(self):
        """读盘即校验：未到期的坏值条目同样回执 + 移出,不等首触"""
        self._write_tasks_file([
            {"id": "bad02", "target_time": "2099-01-01 09:00:00",
             "description": "还没到期的坏值任务", "repeat": "sometimes",
             "repeat_count": None},
        ])

        with patch("auditronclaw.core.tools.builtins.audit_logger") as mock_logger:
            queue = self._run_pacemaker()

        self.assertEqual(len(self._drain(queue)), 0)
        mock_logger.log_event.assert_called_once()
        self.assertEqual(self._read_tasks_file(), [])

    def test_bad_time_format_rejected_with_receipt(self):
        """坏 target_time 格式（原裸 except 吞掉的另一类）同样回执 + 跳过"""
        self._write_tasks_file([
            {"id": "bad03", "target_time": "invalid-time-format",
             "description": "坏时间任务", "repeat": None, "repeat_count": None},
        ])

        with patch("auditronclaw.core.tools.builtins.audit_logger") as mock_logger:
            queue = self._run_pacemaker()

        self.assertEqual(len(self._drain(queue)), 0)
        mock_logger.log_event.assert_called_once()
        self.assertEqual(self._read_tasks_file(), [])


class TestNextOccurrence(unittest.TestCase):
    """_next_occurrence 纯函数：四值算术的硬编码字面量钉子（monthly 此前零覆盖）。"""

    def test_four_values_step_to_next_occurrence(self):
        from auditronclaw.core.heartbeat import _next_occurrence
        dt = datetime(2026, 8, 30, 9, 0, 0)
        self.assertEqual(_next_occurrence(dt, "hourly"),
                         datetime(2026, 8, 30, 10, 0, 0))
        self.assertEqual(_next_occurrence(dt, "daily"),
                         datetime(2026, 8, 31, 9, 0, 0))
        self.assertEqual(_next_occurrence(dt, "weekly"),
                         datetime(2026, 9, 6, 9, 0, 0))
        self.assertEqual(_next_occurrence(dt, "monthly"),
                         datetime(2026, 9, 30, 9, 0, 0))

    def test_month_end_clamped_to_last_day(self):
        """月末按次月最后一天钳制:1月31日→2月28日,闰年→29日,12月跨年"""
        from auditronclaw.core.heartbeat import _next_occurrence
        self.assertEqual(_next_occurrence(datetime(2026, 1, 31, 9, 0, 0), "monthly"),
                         datetime(2026, 2, 28, 9, 0, 0))
        self.assertEqual(_next_occurrence(datetime(2024, 1, 31, 9, 0, 0), "monthly"),
                         datetime(2024, 2, 29, 9, 0, 0))
        self.assertEqual(_next_occurrence(datetime(2025, 12, 31, 9, 0, 0), "monthly"),
                         datetime(2026, 1, 31, 9, 0, 0))


class TestRenewalAcrossRepeatValues(_PacemakerHarness):
    """四种 repeat 经真实心跳循环的接线：到期 → 触发一次 → 续期落盘。"""

    def test_all_four_values_renew_and_fire_once(self):
        """到期任务各触发一次,续期时刻与 _next_occurrence 一致（算术语义
        由其单测的硬编码字面量钉住,此处只钉接线）"""
        from auditronclaw.core.heartbeat import _next_occurrence

        base = datetime.now() - timedelta(minutes=1)
        tasks = [{"id": f"r_{freq}", "target_time": base.strftime(TIME_FORMAT),
                  "description": f"{freq} 循环任务", "repeat": freq,
                  "repeat_count": None}
                 for freq in _repeat_values()]
        self._write_tasks_file(tasks)

        queue = self._run_pacemaker()

        self.assertEqual(len(self._drain(queue)), len(tasks),
                         "每条到期任务应恰好触发一次")
        renewed = {t["id"]: t for t in self._read_tasks_file()}
        self.assertEqual(set(renewed), {f"r_{v}" for v in _repeat_values()},
                         "循环任务触发后必须留存（续期）,不能被删")
        for freq in _repeat_values():
            with self.subTest(repeat=freq):
                self.assertEqual(
                    renewed[f"r_{freq}"]["target_time"],
                    _next_occurrence(base, freq).strftime(TIME_FORMAT))


class TestAddFieldFanoutDemo(_PacemakerHarness):
    """加字段演示测试（04 票裁决：priority 不落库,演示不留生产字段）。

    模型化前加一个字段要扇出约 6 处（dict 字面量、docstring、消费点、
    fixtures……）;模型化后的收益以此演示：加字段 = 模型 1 处（本用例以
    子类代入"改模型"这一步,生产模型一字不动）+ 消费点（读回取用）。
    生产读写链路（model_validate 校验 → 续期 → model_dump 落盘）对
    未声明字段透明,新字段随心跳续期无损往返。
    """

    class PrioritizedTask(ScheduledTask):
        """演示用子类：加 priority 恰好一处。"""

        priority: int | None = None

    def test_new_field_survives_renewal_without_production_change(self):
        self.assertNotIn("priority", ScheduledTask.model_fields,
                         "演示前提：生产模型不声明 priority（纸面实验道具不落库）")

        base = datetime.now() - timedelta(minutes=1)
        self._write_tasks_file([{
            "id": "demo01",
            "target_time": base.strftime(TIME_FORMAT),
            "description": "带未来字段的循环任务",
            "repeat": "daily",
            "repeat_count": None,
            "priority": 2,
        }])

        self._run_pacemaker()

        (renewed,) = self._read_tasks_file()
        self.assertEqual(renewed["target_time"],
                         (base + timedelta(days=1)).strftime(TIME_FORMAT),
                         "携带未声明字段的任务照常续期")
        # 消费点：以加过字段的模型读回,类型化取用新字段
        task = self.PrioritizedTask.model_validate(renewed)
        self.assertEqual(task.priority, 2)


if __name__ == '__main__':
    unittest.main()
