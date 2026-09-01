"""Web 终端脚手架基线:token 鉴权中间件与 web/dist 静态托管。

中间件按纯 ASGI 形态落位,同时覆盖 http 与 websocket 两类 scope
(WS 路由接入时直接复用),故两类都测:无/错 token 一律拒
(http 403 / websocket 拒握手)。首连形态是带 token 的 URL:query
校验通过即下发 cookie,浏览器后续拉取静态资产(HTML 引用里不带
凭据)靠 cookie 过门——资产链路按浏览器真实请求形态测,HTML 探针
探不到这一段。
"""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from entry.web import COOKIE_NAME, create_web_app, generate_token

TOKEN = "probe-token-0123456789abcdef"


class WebServerTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dist_dir = Path(self._tmp.name)
        (self.dist_dir / "index.html").write_text(
            "<html><body>web-terminal-placeholder</body></html>", encoding="utf-8"
        )
        assets = self.dist_dir / "assets"
        assets.mkdir()
        (assets / "app.js").write_text("// placeholder", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _client(self, token: str = TOKEN) -> TestClient:
        return TestClient(create_web_app(token=token, static_dir=self.dist_dir))


class TestTokenGate(WebServerTestBase):
    def test_missing_token_returns_403(self):
        self.assertEqual(self._client().get("/").status_code, 403)

    def test_wrong_token_returns_403(self):
        response = self._client().get("/", params={"token": "wrong"})
        self.assertEqual(response.status_code, 403)

    def test_query_token_serves_placeholder(self):
        response = self._client().get("/", params={"token": TOKEN})
        self.assertEqual(response.status_code, 200)
        self.assertIn("web-terminal-placeholder", response.text)

    def test_assets_without_credentials_rejected(self):
        self.assertEqual(self._client().get("/assets/app.js").status_code, 403)

    def test_websocket_handshake_rejected_without_valid_token(self):
        with self.assertRaises(WebSocketDisconnect) as raised:
            with self._client().websocket_connect("/ws?token=wrong"):
                pass
        self.assertEqual(raised.exception.code, 1008)


class TestFirstConnectCookieFlow(WebServerTestBase):
    """浏览器首连链路:/?token=... 下发 cookie,资产请求凭 cookie 过门。"""

    def test_query_token_sets_hardened_cookie(self):
        response = self._client().get("/", params={"token": TOKEN})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers.get("set-cookie", "")
        self.assertIn(f"{COOKIE_NAME}={TOKEN}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_assets_pass_with_first_connect_cookie(self):
        with self._client() as client:
            client.get("/", params={"token": TOKEN})
            self.assertEqual(client.get("/assets/app.js").status_code, 200)

    def test_wrong_cookie_rejected(self):
        client = self._client()
        client.cookies.set(COOKIE_NAME, "wrong")
        self.assertEqual(client.get("/assets/app.js").status_code, 403)


class TestDistFallback(unittest.TestCase):
    def test_missing_dist_returns_503(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(
                create_web_app(token=TOKEN, static_dir=Path(tmp) / "absent")
            )
            response = client.get("/", params={"token": TOKEN})
            self.assertEqual(response.status_code, 503)


class TestTokenGeneration(unittest.TestCase):
    def test_generated_tokens_unique_per_call(self):
        tokens = {generate_token() for _ in range(8)}
        self.assertEqual(len(tokens), 8)


if __name__ == "__main__":
    unittest.main()
