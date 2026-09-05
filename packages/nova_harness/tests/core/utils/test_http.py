"""core/utils/http 的单元测试：certifi 锚定的默认 SSL context。

冻结形态（PyInstaller）下 Python 缺省 CA 路径为空——stdlib urllib
直连 HTTPS 会 CERTIFICATE_VERIFY_FAILED（包管理下载链的既有事故）；
本模块把 CA 束锚到随行的 certifi。
"""

import ssl
from unittest.mock import patch

import certifi

from nova_harness.core.package.binaries.manager import _download
from nova_harness.core.package.source.resolver import npm_fetch_json
from nova_harness.core.utils.http import default_ssl_context


def test_default_ssl_context_anchors_certifi():
    ctx = default_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    # certifi 的 CA 束真实在盘上（冻结包 _internal/certifi/cacert.pem 同物）
    assert certifi.where().endswith("cacert.pem")


def test_npm_fetch_json_passes_ssl_context():
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"{}"

    def _fake_urlopen(request, timeout=None, context=None):
        captured["context"] = context
        return _Resp()

    with patch(
        "nova_harness.core.package.source.resolver.urllib.request.urlopen",
        side_effect=_fake_urlopen,
    ):
        npm_fetch_json("https://registry.npmjs.org/x")

    assert isinstance(captured.get("context"), ssl.SSLContext)


def test_binaries_download_passes_ssl_context(tmp_path):
    captured = {}

    class _Resp:
        def read(self, *args):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _fake_urlopen(request, timeout=None, context=None):
        captured["context"] = context
        return _Resp()

    with patch(
        "nova_harness.core.package.binaries.manager.urllib.request.urlopen",
        side_effect=_fake_urlopen,
    ):
        _download("https://example.invalid/x", str(tmp_path / "out.bin"))

    assert isinstance(captured.get("context"), ssl.SSLContext)
