"""出站通道注册表与 meta-test 强制（03 票 C）。

出站通道 = 命名网络工具的生产传输路径。注册表：名 → (getter, setter)，
每通道自带哨兵深度——feishu 守注入缝 _http_sender（它本身就是网络边界），
IMAP 守真套接字边界 imaplib.IMAP4_SSL（不得降级为只换注入缝，那会浅一层：
生产 provider 允许被测，守门只挡真实连接）。conftest 遍历本注册表布哨。

meta-test 是强制力：import 网络库的模块必须在注册表有条目，否则红——
"未登记无声进库"变"未登记必红"；并以故意未登记样例验证 meta-test 本身
会红（测试的测试，防 meta-test 空转）。
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auditronclaw.core.tools import egress, feishu_tool, mail_tool

# 包根（meta-test 的扫描范围：出站通道都在包内定义；entry/benchmarks 不定义通道）
PACKAGE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'auditronclaw'))

# 网络库清单：根名匹配覆盖子模块；urllib 家族按限定名精确匹配——
# urllib.request 开网络，urllib.error/parse 只是异常/解析类型，不算
NETWORK_LIB_ROOTS = frozenset({
    "requests", "httpx", "aiohttp", "urllib3", "websockets",
    "imaplib", "smtplib", "poplib", "ftplib", "nntplib", "telnetlib",
    "socket", "http",
})
NETWORK_LIB_QUALIFIED = frozenset({"urllib.request"})


def network_imports_of(tree: "ast.Module") -> set:
    """AST 收集一个模块 import 的网络库名（根名与限定名两种形态，含函数内延迟 import）。"""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in NETWORK_LIB_QUALIFIED:
                    found.add(alias.name)
                elif alias.name.split(".")[0] in NETWORK_LIB_ROOTS:
                    found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in NETWORK_LIB_QUALIFIED:
                found.add(mod)
            elif mod == "urllib":
                for alias in node.names:  # from urllib import request → 网络
                    if f"urllib.{alias.name}" in NETWORK_LIB_QUALIFIED:
                        found.add(f"urllib.{alias.name}")
            elif mod.split(".")[0] in NETWORK_LIB_ROOTS:
                found.add(mod.split(".")[0])
    return found


def assert_module_registered(module: str, imports: set, registered: frozenset) -> None:
    """meta-test 判定核（纯函数）：import 网络库的模块必须在注册表有条目。

    单独成函数是为了能用"故意未登记样例"验证判定核本身会红。
    """
    if imports and module not in registered:
        raise AssertionError(
            f"模块 {module} import 网络库 {sorted(imports)}，但不在出站通道注册表——"
            f"新通道必须在传输定义同文件 register_egress_channel（否则测试哨兵"
            f"不覆盖，真实网络缺口无声进库）。已登记模块：{sorted(registered)}")


def _package_modules_with_network_imports():
    """遍历包内源码，产出 (模块名, 网络库导入集)。"""
    for dirpath, _, filenames in os.walk(PACKAGE_ROOT):
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, os.path.dirname(PACKAGE_ROOT))
            module = rel[:-len(".py")].replace(os.sep, ".")
            if module.endswith(".__init__"):
                module = module[: -len(".__init__")]
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            yield module, network_imports_of(tree)


class TestEgressRegistryShape(unittest.TestCase):
    """注册表形状：条目与传输定义同文件登记，getter/setter 可换装可还原。"""

    def _channel(self, name):
        channels = {ch.name: ch for ch in egress.egress_channels()}
        self.assertIn(name, channels,
                      f"通道 {name} 未登记（应在传输定义同文件登记）")
        return channels[name]

    def test_both_production_channels_registered(self):
        """两条生产通道都在册：feishu webhook POST 与 IMAP SSL 取信"""
        names = {ch.name for ch in egress.egress_channels()}
        self.assertIn("feishu_webhook", names)
        self.assertIn("imap_ssl", names)

    def test_registered_modules_are_transport_definitions(self):
        """条目登记在传输定义所在模块（登记与定义同址的形状钉子）"""
        self.assertEqual(self._channel("feishu_webhook").module,
                         feishu_tool.__name__)
        self.assertEqual(self._channel("imap_ssl").module,
                         mail_tool.__name__)

    def test_feishu_sentinel_depth_at_injection_seam(self):
        """feishu 哨兵深度：注入缝 _http_sender 本身就是网络边界——
        setter 换装必须落在 _http_sender 上（测试注入走 _active_sender，
        哨兵换的是生产通道原函数）"""
        ch = self._channel("feishu_webhook")
        original = ch.getter()
        marker = object()
        try:
            ch.setter(marker)
            self.assertIs(feishu_tool._http_sender, marker,
                          "哨兵必须换在 _http_sender（网络边界）上")
        finally:
            ch.setter(original)
        self.assertIs(feishu_tool._http_sender, original, "退出还原生产通道")

    def test_imap_sentinel_depth_at_socket_boundary(self):
        """IMAP 哨兵深度：真套接字边界 imaplib.IMAP4_SSL，不是注入缝
        _active_provider——降级为只换注入缝会浅一层（生产 provider 允许
        被测，守门只挡真实连接）"""
        import imaplib
        ch = self._channel("imap_ssl")
        original = ch.getter()
        self.assertIs(original, imaplib.IMAP4_SSL,
                      "getter 取到的必须是 imaplib.IMAP4_SSL 本身")
        marker = object()
        try:
            ch.setter(marker)
            self.assertIs(imaplib.IMAP4_SSL, marker,
                          "哨兵必须换在 imaplib.IMAP4_SSL（套接字边界）上")
        finally:
            ch.setter(original)
        self.assertIs(imaplib.IMAP4_SSL, original, "退出还原生产通道")


class TestEgressRegistryMetaTest(unittest.TestCase):
    """meta-test：import 网络库的模块必须在注册表有条目，否则红。"""

    def test_every_network_importer_in_package_is_registered(self):
        """全包扫描零漏登：每条 (模块, 网络库导入) 都过判定核"""
        registered = frozenset(ch.module for ch in egress.egress_channels())
        self.assertTrue(registered, "注册表为空——通道定义模块未导入或未登记")
        offenders = []
        for module, imports in _package_modules_with_network_imports():
            try:
                assert_module_registered(module, imports, registered)
            except AssertionError as e:
                offenders.append(str(e))
        self.assertEqual(offenders, [])

    def test_meta_test_goes_red_on_unregistered_sample(self):
        """故意未登记样例：判定核必须红（测试的测试——防 meta-test 空转，
        假如判定核退化成永绿，上面那条全包扫描就失去了强制力）"""
        registered = frozenset(ch.module for ch in egress.egress_channels())
        with self.assertRaises(AssertionError):
            assert_module_registered("auditronclaw.core.tools.smoke_http",
                                     {"requests"}, registered)

    def test_walker_catches_unregistered_module_in_package(self):
        """遍历器也必须会红：把故意未登记的样例真放进包里扫一次，能被
        找出来——防遍历器因路径/布局变化空转，让全包扫描变成永绿的假关"""
        offender = os.path.join(PACKAGE_ROOT, "core", "tools",
                                "_unregistered_smoke_http.py")
        self.assertFalse(os.path.exists(offender), "样例文件用后必须清理")
        try:
            with open(offender, "w", encoding="utf-8") as f:
                f.write("import requests\n")
            walked = dict(_package_modules_with_network_imports())
            self.assertEqual(walked.get("auditronclaw.core.tools._unregistered_smoke_http"),
                             {"requests"}, "遍历器必须能扫出包内网络导入（含函数外）")
        finally:
            os.remove(offender)

    def test_clean_module_not_flagged(self):
        """不 import 网络库的模块不误伤（判定核只看网络库导入）"""
        assert_module_registered("auditronclaw.core.tools.builtins",
                                 set(), frozenset())


if __name__ == '__main__':
    unittest.main()
