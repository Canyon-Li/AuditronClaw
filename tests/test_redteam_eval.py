"""红队测试:calculator 的注入攻击防御。

验证 AST 节点白名单求值器只接受纯算术表达式。
TDD 流程:本文件先在旧 eval 实现上运行,注入用例必须失败(红),
替换实现后全部通过(绿)。
"""
import unittest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cyberclaw.core.tools.builtins import calculator


# 注入攻击向量:必须在任何结果输出中被拒绝
INJECTION_CASES = [
    # 属性链逃逸:空元组 -> object -> 全部子类,可定位 os.system
    "().__class__.__bases__[0].__subclasses__()",
    # 动态导入
    "__import__('os')",
    # 文件读取
    "open('/etc/passwd').read()",
    # lambda 表达式
    "lambda x: x",
    # 列表推导(含 Call/Name 节点)
    "[i for i in range(10)]",
    # f-string 内嵌调用
    "f\"{__import__('os').getcwd()}\"",
    # eval 自引用
    "eval('2+2')",
    # exec 语句执行
    "exec('import os')",
    # compile 调用
    "compile('1+1', '<s>', 'eval')",
    # 获取内置函数表
    "globals()",
    # 布尔/None 常量探测(非数字常量)
    "True + True",
    # 名称引用
    "pi",
    # 超长嵌套括号(健壮性)
    "((((((((((1))))))))))+" * 200,
    # 空输入
    "",
    # 纯空格
    "   ",
]

# 合法边界用例:必须算出正确结果
VALID_CASES = [
    ("2 + 3", "5"),
    ("2**0.5", "1.4142135623730951"),
    ("-(-3)", "3"),
    ("(1+2)*3/4", "2.25"),
    ("10 % 3", "1"),
    ("7 // 2", "3"),
    ("123456789*987654321", "121932631112635269"),
    ("-5 + +2", "-3"),
]


class TestCalculatorRedTeam(unittest.TestCase):
    """calculator 红队测试:注入拒绝 + 合法边界"""

    def test_injection_attacks_rejected(self):
        """所有注入向量必须被拒绝:返回计算出错文案,且不含任何求值结果"""
        for expr in INJECTION_CASES:
            with self.subTest(expr=expr[:50]):
                result = calculator.invoke({"expression": expr})
                self.assertIn(
                    "计算出错", result,
                    f"注入向量未被拒绝: {expr[:50]!r} -> {result!r}"
                )
                # 拒绝信息不得泄漏回显求值结果
                self.assertNotIn("的计算结果是", result)

    def test_valid_arithmetic_accepted(self):
        """合法算术表达式必须正确求值"""
        for expr, expected in VALID_CASES:
            with self.subTest(expr=expr):
                result = calculator.invoke({"expression": expr})
                self.assertIn(
                    expected, result,
                    f"合法表达式被拒或算错: {expr!r} -> {result!r}"
                )

    def test_division_by_zero_rejected_cleanly(self):
        """除零应返回计算出错,而非抛异常"""
        result = calculator.invoke({"expression": "1 / 0"})
        self.assertIn("计算出错", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
