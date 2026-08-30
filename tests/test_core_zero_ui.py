"""core 零 UI 依赖钉子(会话引擎 03 票)。

agent.py 曾直接 import prompt_toolkit 并 print"正在更新上下文记忆"——
UI 依赖泄漏进 core,headless 场景(基准/CI/未来 Web 终端)被迫拖终端库。
摘除后三道钉子:
- 源码级:core/agent.py 源码不含 prompt_toolkit
- 导入级:子进程 import auditronclaw.core.agent,prompt_toolkit 不进
  sys.modules(测试进程自身可能已被其他模块污染,必须子进程验)
- 行为级:上下文裁剪触发时改发 system_action 审计事件(monitor 已有
  渲染先例 entry/monitor.py),该观测从 TUI 挪到 monitor
"""
import asyncio
import os
import subprocess
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


# ============ 假件:脚本化 LLM + 探针工具(零真实网络,同 01/02 票注入点) ============

@tool
def fake_probe(query: str) -> str:
    """测试探针工具。"""
    return f"probe-ok:{query}"


class ScriptedLLM:
    """假 LLM:按脚本逐条吐 AIMessage(agent_node 内摘要与主答复共用此脚本)。"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        assert self.script, "假 LLM 脚本耗尽"
        return self.script.pop(0)


THREAD_ID = "ui_leak_test"


def _enter_fake_patches(stack, llm):
    """现有注入点三件套 + 强制触发上下文裁剪(同 01 票三重 patch)。

    audit_logger 不在此处假化——钉子三需要拿到 mock 断言调用,由该测试自 patch。
    """
    for p in (
        patch('auditronclaw.core.agent.get_provider', return_value=llm),
        patch('auditronclaw.core.agent.BUILTIN_TOOLS', [fake_probe]),
        patch('auditronclaw.core.agent.load_dynamic_skills', return_value=[]),
        # 强裁剪:让本回合就产生 discarded_msgs(不必真攒 40 回合)
        patch('auditronclaw.core.agent.trim_context_messages',
              return_value=([], [HumanMessage(content="旧对话")])),
    ):
        stack.enter_context(p)


class TestCoreZeroPromptToolkit(unittest.TestCase):
    """钉子一、二:core/agent.py 源码与导入链都零 prompt_toolkit。"""

    def test_agent_source_has_no_prompt_toolkit(self):
        import auditronclaw.core.agent as agent_mod
        src = Path(agent_mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("prompt_toolkit", src,
                         "core/agent.py 不得出现 prompt_toolkit(UI 依赖泄漏)")

    def test_import_agent_does_not_load_prompt_toolkit(self):
        """子进程 import:TUI/终端库不得经 core 的导入链进内存。"""
        code = (
            "import sys\n"
            "import auditronclaw.core.agent\n"
            "leaked = [m for m in sys.modules if m.startswith('prompt_toolkit')]\n"
            "assert not leaked, f'prompt_toolkit 经 core 导入链泄漏: {leaked}'\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=120,
        )
        self.assertEqual(result.returncode, 0,
                         f"子进程导入失败: {result.stderr}")


class TestTrimEmitsSystemAction(unittest.TestCase):
    """钉子三:上下文裁剪触发的观测走 system_action 审计事件,不走 TUI print。"""

    def test_trim_triggers_system_action_log_event(self):
        from auditronclaw.core.agent import create_agent_app

        async def run_turn(app):
            async for _ in app.astream(
                {"messages": [HumanMessage(content="继续聊")]},
                config={"configurable": {"thread_id": THREAD_ID}},
                stream_mode="updates",
            ):
                pass  # 只驱动,不消费(agent_node 的埋点在节点内部)

        with ExitStack() as stack:
            # 脚本两步:① 摘要合成(裁剪分支的 llm.invoke)② 主答复
            llm = ScriptedLLM([AIMessage(content="交接摘要"), AIMessage(content="收尾回复")])
            _enter_fake_patches(stack, llm)
            logger_mock = stack.enter_context(
                patch('auditronclaw.core.agent.audit_logger'))
            app = create_agent_app(
                provider_name="fake",
                model_name="fake-model",
                checkpointer=MemorySaver(),
                thread_id=THREAD_ID,
            )
            asyncio.run(run_turn(app))

        logger_mock.log_event.assert_any_call(
            thread_id=THREAD_ID,
            event="system_action",
            content="正在更新上下文记忆...",
        )


if __name__ == '__main__':
    unittest.main()
