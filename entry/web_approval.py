"""Web 审批应答桥(07 票):引擎应答通道 ↔ WS decision 帧的网络形态。

TUI 的 ApprovalBridge(entry/main.py)把审批交给输入循环的应答步;Web
形态没有"输入循环"这个单持有者——应答来自任意在线 WS 连接的 decision 帧:

- 引擎侧:bridge.responder 作为 SessionEngine 的应答通道,请求挂起成
  future,等 WS 侧回填 ApprovalDecision,引擎同回合续行
- WS 侧:decision 帧 choice 经 parse_decision_choice 映射后回填 future;
  无挂起或已终局即弃置(回 decision_unavailable)

与 TUI 桥的两处刻意差别:
- 不在断线时收口:浏览器刷新/断线重连后,同一笔审批仍可应答(挂起在
  属主进程,不在连接上);无人应答由引擎超时兜底——不答即拒、拒绝留痕
- responder 同步注册(普通函数返回 future,不是 async def):worker
  广播 approval_request 与引擎恢复执行之间没有事件环拍,decision 帧
  处理时挂起必然已入槽;async 注册会留出"帧先到、future 未立"的竞态
  窗口,-answer 空转一次,操作员看见莫名的 decision_unavailable

单 worker 逐回合、引擎逐个打断逐个应答:挂起至多一笔,单槽即诚实。
超时语义全在引擎(wait_for 掐死 future):桥只递送请求与决定,不裁决。
"""
import asyncio
from typing import NamedTuple, Optional

from auditronclaw.core.approval.gate import ApprovalDecision, DecisionSource
from auditronclaw.core.session import ApprovalRequest


class _Pending(NamedTuple):
    """单槽挂起条目:审批请求与其应答 future 同进同退。"""

    request: ApprovalRequest
    future: "asyncio.Future[ApprovalDecision]"


def parse_decision_choice(choice) -> Optional[ApprovalDecision]:
    """decision 帧 choice 解析(纯函数):once/always/deny;其余 None。

    三选与 TUI y/a/n 同义:deny 是人的明确拒绝(source=user_once,拒绝
    话术按来路说话),不是无人值守;always 由门内入规则生效。
    """
    if choice == "once":
        return ApprovalDecision(approved=True, persist=False,
                                source=DecisionSource.USER_ONCE)
    if choice == "always":
        return ApprovalDecision(approved=True, persist=True,
                                source=DecisionSource.USER_PERSIST)
    if choice == "deny":
        return ApprovalDecision(approved=False, persist=False,
                                source=DecisionSource.USER_ONCE)
    return None


class WebApprovalBridge:
    """审批应答桥:引擎应答通道(挂起 future)↔ WS decision 帧(回填)。"""

    def __init__(self):
        self._pending: Optional[_Pending] = None

    @property
    def pending(self) -> Optional[ApprovalRequest]:
        """当前挂起未决的审批(无则 None);终局即时出槽,不滞留。"""
        pair = self._pending
        if pair is not None and not pair.future.done():
            return pair.request
        return None

    def responder(self, request: ApprovalRequest) -> "asyncio.Future[ApprovalDecision]":
        """引擎应答通道:请求挂起成 future 即刻入槽,交引擎等待。

        同步函数返回 future(满足 ApprovalResponder 的
        Union[ApprovalDecision, Awaitable] 契约):注册先于任何 await,
        广播-应答竞态在构造上关门(见模块 docstring)。
        """
        fut = asyncio.get_running_loop().create_future()
        self._pending = _Pending(request=request, future=fut)
        fut.add_done_callback(self._drop_done)
        return fut

    def _drop_done(self, fut: "asyncio.Future") -> None:
        """终局条目出槽(回填/超时取消都走到):挂起面不谎报。"""
        if self._pending is not None and self._pending.future is fut:
            self._pending = None

    def answer(self, decision: ApprovalDecision) -> bool:
        """decision 帧回填:无挂起或已终局返回 False(答案弃置)。"""
        pair = self._pending
        if pair is None:
            return False
        if pair.future.done():
            return False
        pair.future.set_result(decision)
        return True
