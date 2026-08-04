import unittest
import os
import sys
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auditronclaw.core.context import trim_context_messages, AgentState


class TestContextTrimming(unittest.TestCase):

    def test_trim_with_system_message_keep_all(self):
        """测试保留所有消息的情况（不超过阈值）"""
        messages = [
            SystemMessage(content="系统消息"),
            HumanMessage(content="用户消息1"),
            AIMessage(content="AI消息1"),
            HumanMessage(content="用户消息2"),
            AIMessage(content="AI消息2")
        ]

        kept, discarded = trim_context_messages(messages, trigger_turns=10, keep_turns=10)

        # 由于回合数(2) < 触发阈值(10)，不应裁剪
        self.assertEqual(len(kept), 5)  # 包含系统消息
        self.assertEqual(len(discarded), 0)

    def test_trim_with_system_message_discard_some(self):
        """测试裁剪部分消息的情况"""
        messages = [
            SystemMessage(content="系统消息"),
            HumanMessage(content="用户消息1"),
            AIMessage(content="AI消息1"),
            HumanMessage(content="用户消息2"),
            AIMessage(content="AI消息2"),
            HumanMessage(content="用户消息3"),
            AIMessage(content="AI消息3"),
            HumanMessage(content="用户消息4"),
            AIMessage(content="AI消息4"),
            HumanMessage(content="用户消息5"),
            AIMessage(content="AI消息5")
        ]

        kept, discarded = trim_context_messages(messages, trigger_turns=3, keep_turns=2)

        # 由于回合数(5) > 触发阈值(3)，应裁剪
        # 保留最后2个回合 + 系统消息 = 5条消息
        self.assertEqual(len(kept), 5)
        self.assertEqual(len(discarded), 6)  # 前3个回合的消息

        # 验证系统消息在保留的消息中
        self.assertIsInstance(kept[0], SystemMessage)

        # 验证保留的是最后2个回合
        self.assertIsInstance(kept[1], HumanMessage)
        self.assertIsInstance(kept[2], AIMessage)
        self.assertIsInstance(kept[3], HumanMessage)
        self.assertIsInstance(kept[4], AIMessage)

    def test_trim_without_system_message(self):
        """测试没有系统消息时的裁剪"""
        messages = [
            HumanMessage(content="用户消息1"),
            AIMessage(content="AI消息1"),
            HumanMessage(content="用户消息2"),
            AIMessage(content="AI消息2"),
            HumanMessage(content="用户消息3"),
            AIMessage(content="AI消息3")
        ]

        kept, discarded = trim_context_messages(messages, trigger_turns=2, keep_turns=1)

        # 回合数(3) > 触发阈值(2)，保留最后1个回合
        self.assertEqual(len(kept), 2)  # 最后一个回合(Human+AI)
        self.assertEqual(len(discarded), 4)  # 前2个回合

    def test_trim_only_system_message(self):
        """测试只有系统消息的情况"""
        messages = [
            SystemMessage(content="系统消息")
        ]

        kept, discarded = trim_context_messages(messages, trigger_turns=1, keep_turns=1)

        self.assertEqual(len(kept), 1)
        self.assertEqual(len(discarded), 0)
        self.assertIsInstance(kept[0], SystemMessage)

    def test_trim_empty_messages(self):
        """测试空消息列表"""
        messages = []

        kept, discarded = trim_context_messages(messages, trigger_turns=1, keep_turns=1)

        self.assertEqual(len(kept), 0)
        self.assertEqual(len(discarded), 0)

    def test_trim_with_tool_messages(self):
        """测试包含工具消息的裁剪"""
        messages = [
            SystemMessage(content="系统消息"),
            HumanMessage(content="用户消息1"),
            AIMessage(content="AI消息1"),
            ToolMessage(content="工具结果1", tool_call_id="1"),
            HumanMessage(content="用户消息2"),
            AIMessage(content="AI消息2"),
            ToolMessage(content="工具结果2", tool_call_id="2"),
            HumanMessage(content="用户消息3"),
            AIMessage(content="AI消息3")
        ]

        kept, discarded = trim_context_messages(messages, trigger_turns=2, keep_turns=1)

        # 3个回合(每回合可能包含多个消息) > 阈值2，保留最后1个回合
        # 最后一个回合：HumanMessage + AIMessage
        # 所以前面两回合的所有消息都被丢弃
        self.assertEqual(len(discarded), 6)  # 前两个回合加上系统消息
        self.assertEqual(len(kept), 3)  # 最后一个回合的Human + AI

    def test_turn_calculation_logic(self):
        """测试回合计算逻辑"""
        messages = [
            HumanMessage(content="用户消息1"),
            AIMessage(content="AI消息1a"),
            ToolMessage(content="工具结果1a", tool_call_id="1a"),
            AIMessage(content="AI消息1b"),
            ToolMessage(content="工具结果1b", tool_call_id="1b"),
            HumanMessage(content="用户消息2"),
            AIMessage(content="AI消息2"),
            HumanMessage(content="用户消息3"),
            AIMessage(content="AI消息3a"),
            ToolMessage(content="工具结果3a", tool_call_id="3a"),
            AIMessage(content="AI消息3b")
        ]

        # 测试回合是如何计算的
        # 回合1: Human1, AI1a, Tool1a, AI1b, Tool1b
        # 回合2: Human2, AI2
        # 回合3: Human3, AI3a, Tool3a, AI3b
        # 总共3个回合

        kept, discarded = trim_context_messages(messages, trigger_turns=2, keep_turns=1)

        # 3回合 > 阈值2，保留最后1回合
        self.assertEqual(len(kept), 4)  # Human3, AI3a, Tool3a, AI3b
        self.assertEqual(len(discarded), 7)  # 前两个回合的所有消息


class TestAgentState(unittest.TestCase):

    def test_agent_state_initialization(self):
        """测试AgentState的初始化"""
        initial_state = AgentState(
            messages=[],
            summary=""
        )

        self.assertEqual(initial_state["messages"], [])
        self.assertEqual(initial_state["summary"], "")

    def test_agent_state_with_messages(self):
        """测试带消息的AgentState"""
        messages = [
            HumanMessage(content="用户消息"),
            AIMessage(content="AI消息")
        ]

        state = AgentState(
            messages=messages,
            summary="测试摘要"
        )

        self.assertEqual(len(state["messages"]), 2)
        self.assertEqual(state["summary"], "测试摘要")


if __name__ == '__main__':
    unittest.main()