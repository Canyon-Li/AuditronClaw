"""测试共享注入件:三个测试文件共用的传输层假实现与注入上下文(注入点 B 的测试侧)。"""


class FakeSender:
    """测试假发送器:捕获发送内容与目标域,可注入异常模拟失败,零真实网络。"""

    def __init__(self, response=None, error=None):
        self.sent = []
        self.response = response or {"code": 0, "msg": "success"}
        self.error = error

    def __call__(self, webhook_url, payload):
        if self.error:
            raise self.error
        self.sent.append((webhook_url, payload))
        return self.response


class InjectedSender:
    """上下文管理器:模块内注入假 sender,退出时还原生产通道。"""

    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        from auditronclaw.core.tools import feishu_tool
        feishu_tool.set_sender(self.fake)
        return self.fake

    def __exit__(self, *exc):
        from auditronclaw.core.tools import feishu_tool
        feishu_tool.set_sender(None)
        return False


class InjectedProvider:
    """上下文管理器:模块内注入取信层 provider,退出时还原生产通道。"""

    def __init__(self, provider):
        self.provider = provider

    def __enter__(self):
        from auditronclaw.core.tools import mail_tool
        mail_tool.set_provider(self.provider)
        return self.provider

    def __exit__(self, *exc):
        from auditronclaw.core.tools import mail_tool
        mail_tool.set_provider(None)
        return False
