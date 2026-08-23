import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

from run_golden_eval import judge as golden_judge


def _raw(reply="", tool_calls=None, pushes=None):
    return {
        "case_id": "t",
        "surface": "mailbox_desk",
        "tool_calls": tool_calls or [],
        "reply": reply,
        "pushes": pushes or [],
    }


class TestGoldenPushContains(unittest.TestCase):
    """push_contains 判定语义(邮箱事务台 golden 断言新增形态)。

    语义钉死:假 sender 捕获的任一次推送文本含期望串 = 命中。
    捕获列表来自 pipeline 注入缝,判定器只做纯字符串包含。
    """

    CASE = {
        "id": "x", "surface": "mailbox_desk", "expect": "passed",
        "asserts": [{"type": "push_contains", "contains": "跨类别待办"}],
    }

    def test_hit_when_any_push_contains(self):
        v = golden_judge(_raw(pushes=["邮箱事务台日报 | 共4封 · 跨类别待办 2 项\n■ 待办…"]), self.CASE)
        self.assertTrue(v["passed"])

    def test_multiple_pushes_any_hit(self):
        v = golden_judge(_raw(pushes=["第一次推送失败重试文本", "分类账重发 · 跨类别待办 1 项"]), self.CASE)
        self.assertTrue(v["passed"])

    def test_miss_when_no_push_at_all(self):
        v = golden_judge(_raw(pushes=[]), self.CASE)
        self.assertFalse(v["passed"])
        self.assertIn("push_contains", v["misses"][0])

    def test_miss_when_push_lacks_keyword(self):
        v = golden_judge(_raw(pushes=["今天没有邮件"]), self.CASE)
        self.assertFalse(v["passed"])

    def test_absent_pushes_field_is_miss_not_error(self):
        """旧 runner 产物没有 pushes 字段:按未推送处理,不抛异常(判定器不信任输入完整性)"""
        raw = {"case_id": "t", "surface": "mailbox_desk",
               "tool_calls": [], "reply": "done"}
        v = golden_judge(raw, self.CASE)
        self.assertFalse(v["passed"])


if __name__ == "__main__":
    unittest.main()
