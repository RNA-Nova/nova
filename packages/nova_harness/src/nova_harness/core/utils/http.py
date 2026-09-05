"""HTTPS 公共件：CA 束锚定 certifi 的默认 SSL context。

冻结形态（PyInstaller）下 Python 缺省 CA 路径为空（用户机器没有
Python.org 安装目录），stdlib ``urllib.request.urlopen`` 的默认
context 会报 ``CERTIFICATE_VERIFY_FAILED``；部分最小化 Linux 环境同病。
certifi 经 httpx 依赖链随行（冻结包内含 cacert.pem），统一锚定它。

注：模型 API 走 httpx/openai（内部已锚 certifi），不受影响；本模块服务
的是直接使用 stdlib urllib 的调用方（包管理下载链等）。
"""

import ssl

import certifi


def default_ssl_context() -> ssl.SSLContext:
    """CA 束锚定 certifi 的默认 HTTPS context。"""
    return ssl.create_default_context(cafile=certifi.where())
