"""依赖方向 meta-test(票 04):core 不 import 任何域(ADR-002 依赖方向)。

domains → core 单向;core 反向 import 域会让基座与域互相拖累(改域即改基座),
方向必须机器把守、不靠评审眼力。装配点(create_agent_app 所在的 agent.py)
是唯一例外——显式 import 加调用 register() 是接线本职(ADR-002)。

扫描面是 AST 的 Import / ImportFrom 节点(绝对与相对两种形态,相对导入按
所在包解析成绝对名再判);动态字符串导入(importlib.import_module("…"))
不在静态扫描面,由 PR 评审兜底——本钉子的边界如此声明。
"""
import ast
import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, REPO_ROOT)
CORE_DIR = os.path.join(REPO_ROOT, "auditronclaw", "core")
# 唯一豁免:装配点。豁免不是免检——见 test_assembly_point_is_the_only_domain_importer
ASSEMBLY_POINT = os.path.join(CORE_DIR, "agent.py")

_DOMAINS_ROOT = "auditronclaw.domains"


def _touches_domains(module_name: str) -> bool:
    """模块名是否落进域包(auditronclaw.domains 或其子模块)。"""
    return (module_name == _DOMAINS_ROOT
            or module_name.startswith(_DOMAINS_ROOT + "."))


def _domain_import_violations(source: str, package: str) -> list:
    """源码里指向域包的 import 清单(人可读描述;真实文件与合成样本共用)。

    package 是该源码所属包的绝对名(如 auditronclaw.core.approval),
    相对导入按它解析。from-import 的两种入域形态都算:模块路径直接落进
    域包,或经父包转手取域子包(from auditronclaw import domains)。
    """
    violations = []
    parts = package.split(".") if package else []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _touches_domains(alias.name):
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = []  # 绝对导入:模块名自足,不挂所在包前缀
            else:
                if node.level - 1 > len(parts):
                    continue  # 逃出顶层包的相对导入本就是运行期错误,到不了域包
                base = parts[:len(parts) - (node.level - 1)]
            module_parts = node.module.split(".") if node.module else []
            prefix = ".".join(base + module_parts)
            for alias in node.names:
                candidate = f"{prefix}.{alias.name}" if prefix else alias.name
                if _touches_domains(prefix) or _touches_domains(candidate):
                    relative = "." * node.level + (node.module or "")
                    violations.append(
                        f"from {relative} import {alias.name} → {candidate}")
    return violations


def _package_of(file_path: str) -> str:
    """文件路径 → 所属包绝对名(auditronclaw/core/approval/roster.py →
    auditronclaw.core.approval;__init__.py 归其所在包)。"""
    rel = os.path.relpath(file_path, REPO_ROOT)
    parts = rel[:-len(".py")].replace(os.sep, ".").split(".")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class TestCoreDomainImportScan(unittest.TestCase):
    """core 全树零域 import(装配点唯一例外);扫描器自身要先被验明会咬人。"""

    def test_checker_catches_synthetic_violations(self):
        """扫描器要咬人:合成违规样本逐条命中,core 内部相对导入不误伤。"""
        core, approval = "auditronclaw.core", "auditronclaw.core.approval"
        samples = [
            ("import auditronclaw.domains.feishu.tool", core, 1),
            ("from auditronclaw.domains.feishu import tool", approval, 1),
            ("from ..domains.feishu import tool", core, 1),      # 装配点同款形态
            ("from ...domains.feishu import tool", approval, 1),  # 嵌套包三级上跳
            ("from auditronclaw import domains", core, 1),        # 经父包转手取域包
            ("from .. import domains", core, 1),                  # 相对形态的转手
            # 不误伤:core 内部相对导入(含所在包里不存在的路径)与第三方库
            ("from .domains.feishu import tool", approval, 0),
            ("from .domain import DomainRegistration", approval, 0),
            ("from ...core.domain import DomainRegistration", approval, 0),
            ("from langchain_core.tools import BaseTool", core, 0),
        ]
        for statement, package, expected_count in samples:
            with self.subTest(statement=statement, package=package):
                found = _domain_import_violations(statement, package)
                self.assertEqual(len(found), expected_count,
                                 f"{statement} → {found}")

    def test_core_never_imports_domains(self):
        """core 全树(装配点除外)零域 import——依赖方向不被悄悄反向。"""
        violations = {}
        scanned = 0
        for dirpath, _dirnames, filenames in os.walk(CORE_DIR):
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                if os.path.abspath(path) == ASSEMBLY_POINT:
                    continue
                scanned += 1
                with open(path, encoding="utf-8") as f:
                    source = f.read()
                found = _domain_import_violations(source, _package_of(path))
                if found:
                    violations[os.path.relpath(path, REPO_ROOT)] = found
        self.assertGreaterEqual(scanned, 20,
                                f"扫描只看了 {scanned} 个文件——core 目录结构变了,"
                                f"下限失真,请更新本断言")
        self.assertEqual(violations, {},
                         "core import 了域包(依赖方向反向):\n"
                         + "\n".join(f"{f}: {v}" for f, v in violations.items()))

    def test_assembly_point_is_the_only_domain_importer(self):
        """豁免不是免检:装配点必须真的在接线(至少一处域 import)——
        装配点零域 import 意味着域没接线(或接线挪去了别处),豁免成了空文。"""
        with open(ASSEMBLY_POINT, encoding="utf-8") as f:
            found = _domain_import_violations(f.read(), "auditronclaw.core")
        self.assertTrue(found, "装配点没有 import 任何域——域接线去哪了?")


if __name__ == '__main__':
    unittest.main()
