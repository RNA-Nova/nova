"""测试共享的 FakeTransport：录制请求/通知流量，按方法名编程响应"""


class FakeTransport:
    """实现 Transport 接口的内存假传输（协议逻辑单测用）"""

    def __init__(self, responses: dict | None = None):
        self.requests: list[tuple[str, dict, str | None]] = []
        self.notifications: list[tuple[str, dict, str | None]] = []
        self.responses = responses or {}
        self.handlers: list = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def send_request(self, method, params=None, *, channel=None):
        self.requests.append((method, params or {}, channel))
        resp = self.responses.get(method, {})
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def send_notification(self, method, params=None, *, channel=None):
        self.notifications.append((method, params or {}, channel))

    def on_notification(self, handler) -> None:
        self.handlers.append(handler)

    @property
    def is_connected(self) -> bool:
        return self.connected
