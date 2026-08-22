import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

from run_golden_eval import judge


def _raw(reply="", tool_calls=None):
    return {
        "case_id": "t",
        "surface": "file_ops",
        "tool_calls": tool_calls or [],
        "reply": reply,
    }


class TestGoldenJudgeAnyOf(unittest.TestCase):
    """judge 的 any-of 语义（gold_file_006 断言修复配套）。

    作者错误修复的度量前提：任务"读取并回答"不预设具体工具与回复语言。
    required_tool_call 支持 tools 列表（任一命中即过），reply_keyword 支持
    keywords 列表（任一命中即过）；单数旧形态保持兼容。
    """

    def test_required_tool_any_of(self):
        case = {"id": "x", "surface": "file_ops", "expect": "passed",
                "asserts": [{"type": "required_tool_call",
                             "tools": ["read_office_file", "execute_office_shell"]}]}
        # shell 路径命中也算过
        v = judge(_raw(tool_calls=[{"tool": "execute_office_shell",
                                    "args": {"command": "grep -c ERROR logs/error.log"}}]), case)
        self.assertTrue(v["passed"])
        # read 路径命中也算过（旧行为不回退）
        v = judge(_raw(tool_calls=[{"tool": "read_office_file",
                                    "args": {"filepath": "logs/error.log"}}]), case)
        self.assertTrue(v["passed"])
        # 都没调 → 失败
        v = judge(_raw(tool_calls=[{"tool": "calculator", "args": {}}]), case)
        self.assertFalse(v["passed"])

    def test_reply_keyword_any_of(self):
        case = {"id": "x", "surface": "file_ops", "expect": "passed",
                "asserts": [{"type": "reply_keyword", "keywords": ["超时", "timeout"]}]}
        self.assertTrue(judge(_raw(reply="共 2 次 db connection timeout"), case)["passed"])
        self.assertTrue(judge(_raw(reply="共 2 次，都是连接超时"), case)["passed"])
        self.assertFalse(judge(_raw(reply="共 2 次"), case)["passed"])

    def test_singular_forms_still_work(self):
        case = {"id": "x", "surface": "file_ops", "expect": "passed",
                "asserts": [{"type": "required_tool_call", "tool": "read_office_file"},
                            {"type": "reply_keyword", "keyword": "超时"}]}
        ok = judge(_raw(reply="超时", tool_calls=[{"tool": "read_office_file", "args": {}}]), case)
        self.assertTrue(ok["passed"])
        bad = judge(_raw(reply="超时", tool_calls=[]), case)
        self.assertFalse(bad["passed"])


if __name__ == "__main__":
    unittest.main()
