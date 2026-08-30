import unittest
from unittest.mock import MagicMock, patch, mock_open
import os
import platform
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auditronclaw.core.tools.sandbox_tools import (
    build_office_tools,
    _get_safe_path,
)


class OfficeToolsTestBase(unittest.TestCase):
    """office 工具测试公共件：临时工位一次装配（05 票起 office 目录经工厂注入，
    不再触仓库 workspace/office）。"""

    @classmethod
    def setUpClass(cls):
        cls.office_dir = tempfile.mkdtemp(prefix="sandbox_tools_test_")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.office_dir, ignore_errors=True)

    def setUp(self):
        self.tools = {t.name: t for t in build_office_tools(self.office_dir)}

    @property
    def list_office_files(self):
        return self.tools["list_office_files"]

    @property
    def read_office_file(self):
        return self.tools["read_office_file"]

    @property
    def write_office_file(self):
        return self.tools["write_office_file"]

    @property
    def execute_office_shell(self):
        return self.tools["execute_office_shell"]


class TestSandboxTools(OfficeToolsTestBase):

    def test_get_safe_path_normal(self):
        """测试正常路径连接（office 目录为装配入参）"""
        result = _get_safe_path(self.office_dir, 'subdir/file.txt')
        expected = os.path.abspath(os.path.join(self.office_dir, 'subdir/file.txt'))
        self.assertEqual(result, expected)

    def test_get_safe_path_traversal_attempt(self):
        """测试路径遍历攻击"""
        with self.assertRaises(PermissionError):
            _get_safe_path(self.office_dir, '../../forbidden/file.txt')

    @patch('auditronclaw.core.tools.sandbox_tools.os.path.exists', return_value=True)
    @patch('auditronclaw.core.tools.sandbox_tools.os.listdir', return_value=['file1.txt', 'subdir'])
    @patch('auditronclaw.core.tools.sandbox_tools.os.path.isdir', side_effect=lambda x: x.endswith('subdir'))
    def test_list_office_files(self, mock_isdir, mock_listdir, mock_exists):
        """测试列出办公文件功能"""
        # 工具需要通过 .invoke() 调用
        result = self.list_office_files.invoke({"sub_dir": ""})

        # 验证函数调用了正确的路径检查
        mock_exists.assert_called_once()
        mock_listdir.assert_called_once()

        # 检查返回结果包含预期元素
        self.assertIn("📄 file1.txt", result)
        self.assertIn("📁 subdir", result)

    @patch('auditronclaw.core.tools.sandbox_tools.os.path.exists', return_value=False)
    def test_list_office_files_nonexistent_dir(self, mock_exists):
        """测试列出不存在目录的文件"""
        result = self.list_office_files.invoke({"sub_dir": "nonexistent"})
        self.assertIn("目录不存在", result)

    @patch('auditronclaw.core.tools.sandbox_tools.os.path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data="file content")
    def test_read_office_file_success(self, mock_file, mock_exists):
        """测试成功读取办公文件"""
        result = self.read_office_file.invoke({"filepath": "test.txt"})
        self.assertEqual(result, "file content")
        mock_file.assert_called_once()

    @patch('auditronclaw.core.tools.sandbox_tools.os.path.exists', return_value=False)
    def test_read_office_file_nonexistent(self, mock_exists):
        """测试读取不存在的办公文件"""
        result = self.read_office_file.invoke({"filepath": "nonexistent.txt"})
        self.assertIn("文件不存在", result)

    @patch('builtins.open', new_callable=mock_open)
    @patch('os.makedirs')
    def test_write_office_file_success(self, mock_makedirs, mock_file):
        """测试成功写入办公文件"""
        result = self.write_office_file.invoke({"filepath": "test.txt", "content": "test content", "mode": "w"})
        self.assertIn("成功以 覆盖/新建 模式写入文件", result)
        mock_file.assert_called_once()
        mock_makedirs.assert_called_once()

    def test_write_office_file_invalid_mode(self):
        """测试写入办公文件 - 无效模式"""
        result = self.write_office_file.invoke({"filepath": "test.txt", "content": "test content", "mode": "x"})
        self.assertIn("❌ 错误：mode 参数必须是", result)

    @patch('auditronclaw.core.tools.sandbox_tools.subprocess.run')
    def test_execute_office_shell_safe_command(self, mock_subprocess):
        """测试执行安全的 shell 命令"""
        # Mock subprocess 结果
        mock_result = mock_subprocess.return_value
        mock_result.returncode = 0
        mock_result.stdout = "command output"
        mock_result.stderr = ""

        result = self.execute_office_shell.invoke({"command": "ls"})
        # 输出格式包含前缀空格和中文冒号 - 使用更宽松的匹配
        self.assertIn("ls", result)
        self.assertIn("command output", result)

    def test_execute_office_shell_dangerous_commands(self):
        """测试执行危险命令会被拦截"""
        dangerous_commands = [
            "cd ../",
            "cat /etc/passwd",
            "ls ~",
            "dir \\",
            "type C:\\windows\\system32\\config\\sam"
        ]

        for cmd in dangerous_commands:
            with self.subTest(cmd=cmd):
                result = self.execute_office_shell.invoke({"command": cmd})
                self.assertIn("❌ 权限拒绝", result)


class TestOfficePathBaseUnification(OfficeToolsTestBase):
    """office/office/ 双写陷阱回归：统一路径基准。

    陷阱病理：write_office_file 收到 "office/config/app.ini" 会静默落到
    office/office/config/app.ini 并返回成功——同一逻辑路径随调用方是否带
    office/ 前缀解析到两个物理文件（改了其实没改）。修复基准：office 根
    即路径基准，冗余首段 "office/" 剥除，"office/x" 与 "x" 必须同文件。
    """

    def test_get_safe_path_strips_redundant_office_prefix(self):
        """冗余 office/ 前缀归一到 office 根基准"""
        self.assertEqual(
            _get_safe_path(self.office_dir, "office/config/app.ini"),
            os.path.abspath(os.path.join(self.office_dir, "config/app.ini")),
        )
        # 仅 "office" 本身 → office 根
        self.assertEqual(
            _get_safe_path(self.office_dir, "office"),
            os.path.abspath(self.office_dir))
        # 平台语义（CI 真实运行教训：本地 Windows 绿 ≠ Linux 绿）：
        # Windows 反斜杠等价分隔符、大小写不敏感 → 剥除；
        # Linux 反斜杠与大写 Office 是合法文件名字符 → 保持原样（不做静默重定向）
        is_win = platform.system() == "Windows"
        self.assertEqual(
            _get_safe_path(self.office_dir, "office\\config\\app.ini"),
            os.path.abspath(os.path.join(
                self.office_dir,
                "config/app.ini" if is_win else "office\\config\\app.ini",
            )),
        )
        self.assertEqual(
            _get_safe_path(self.office_dir, "Office/config/app.ini"),
            os.path.abspath(os.path.join(
                self.office_dir,
                "config/app.ini" if is_win else "Office/config/app.ini",
            )),
        )

    def test_get_safe_path_without_prefix_unchanged(self):
        """无前缀路径不受归一化影响"""
        self.assertEqual(
            _get_safe_path(self.office_dir, "config/app.ini"),
            os.path.abspath(os.path.join(self.office_dir, "config/app.ini")),
        )

    def test_traversal_still_blocked_after_normalization(self):
        """剥前缀不得打开越界口子："office/../.." 类路径仍被拦"""
        with self.assertRaises(PermissionError):
            _get_safe_path(self.office_dir, "office/../../etc/passwd")
        # 反斜杠越界形态仅 Windows 视为分隔符；Linux 下它是 office 内的
        # 字面文件名，不构成越界（钉住"不做静默重定向"的平台语义）
        if platform.system() == "Windows":
            with self.assertRaises(PermissionError):
                _get_safe_path(self.office_dir, "office\\..\\..\\forbidden.txt")
        else:
            self.assertTrue(
                _get_safe_path(self.office_dir, "office\\..\\..\\forbidden.txt")
                .startswith(os.path.abspath(self.office_dir)))

    def test_write_no_silent_double_write_e2e(self):
        """端到端复现：带前缀与不带前缀写入必须落同一物理文件"""
        probe = "_dbl_write_probe.txt"
        probe_path = os.path.join(self.office_dir, probe)
        nested_path = os.path.join(self.office_dir, "office", probe)

        def _cleanup():
            for p in (probe_path, nested_path):
                if os.path.exists(p):
                    os.remove(p)
            nested_dir = os.path.join(self.office_dir, "office")
            if os.path.isdir(nested_dir) and not os.listdir(nested_dir):
                os.rmdir(nested_dir)

        # 前置清残骸（红测试历史遗留），保证断言不受文件系统历史影响
        _cleanup()
        self.addCleanup(_cleanup)
        # 带前缀写 v1，不带前缀覆盖 v2
        self.write_office_file.invoke({"filepath": f"office/{probe}", "content": "v1"})
        self.write_office_file.invoke({"filepath": probe, "content": "v2", "mode": "w"})
        # 不允许出现 office/office/ 双层目录
        self.assertFalse(os.path.exists(nested_path))
        # 两种读法必须读到同一文件（v2）
        self.assertEqual(self.read_office_file.invoke({"filepath": probe}), "v2")
        self.assertEqual(
            self.read_office_file.invoke({"filepath": f"office/{probe}"}), "v2")


class TestShellOfficePrefixGuard(OfficeToolsTestBase):
    """路径基准统一的 shell 侧闭合（gold_file_006 回归）。

    病理：触发语/用户说话常带 office/ 前缀，文件工具已归一化，但模型把
    前缀原样塞进 shell 命令时（cwd 已是 office 根）会拼出 office/office/…
    找不到文件，模型连撞三次后放弃。守卫：shell 参数带 office/ 前缀时
    拒绝并给出可自纠提示（去掉前缀重试），静默分歧变引导式纠正。
    """

    def test_shell_office_prefixed_arg_rejected_with_guidance(self):
        # 反斜杠前缀仅 Windows 触发守卫；Linux 下它是字面文件名参数，
        # 不构成冗余前缀（走到执行层，由"文件不存在"自然反馈）
        cmds = [
            "cat office/logs/error.log",
            "grep -c ERROR office/logs/error.log",
        ]
        if platform.system() == "Windows":
            cmds.append("type office\\logs\\error.log")
        else:
            result = self.execute_office_shell.invoke(
                {"command": "type office\\logs\\error.log"})
            self.assertNotIn("office 根", result)
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                result = self.execute_office_shell.invoke({"command": cmd})
                self.assertIn("❌ 权限拒绝", result)
                self.assertIn("office 根", result)

    def test_shell_plain_relative_arg_not_caught_by_guard(self):
        """无前缀相对路径不受守卫影响（走到执行层，不因本守卫拒绝）"""
        result = self.execute_office_shell.invoke({"command": "cat logs/nonexistent.log"})
        self.assertNotIn("冗余 office", result)
        self.assertNotIn("office 根", result)


class TestFindExecGuard(OfficeToolsTestBase):
    """find 执行族参数守卫：白名单对非解释器段只看段首，find 携带
    -exec/-delete/-fprint 族参数时能执行任意命令/写删文件——段首放行
    等于整段放行。守卫：find 段出现这些参数即拒绝；纯搜索用法不受影响。
    （审批门分级器对同一参数集按执行类必批，两处共用同一份参数清单。）
    """

    def test_find_with_exec_family_flags_rejected(self):
        cmds = [
            "find . -exec python evil.py ;",
            "find . -name x -delete",
            "find . -fprintf out.txt %p",
            "find . -execdir rm {} +",
        ]
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                result = self.execute_office_shell.invoke({"command": cmd})
                self.assertIn("❌ 权限拒绝", result)
                self.assertIn("find", result, "拒绝信息要点名触发命令")

    def test_plain_find_search_not_caught_by_guard(self):
        """纯搜索用法走到执行层，不因本守卫拒绝"""
        with patch('auditronclaw.core.tools.sandbox_tools.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = self.execute_office_shell.invoke({"command": "find . -name '*.log'"})
        self.assertNotIn("❌ 权限拒绝", result)


if __name__ == '__main__':
    unittest.main()
