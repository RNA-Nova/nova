"""PKCE 生成测试。"""

import base64
import hashlib

from nova_ai.auth.oauth.pkce import generate_pkce


def test_generate_pkce_returns_verifier_and_challenge():
    pkce = generate_pkce()
    assert "verifier" in pkce
    assert "challenge" in pkce
    assert isinstance(pkce["verifier"], str)
    assert isinstance(pkce["challenge"], str)


def test_generate_pkce_verifier_is_urlsafe_base64_without_padding():
    verifier = generate_pkce()["verifier"]
    assert "=" not in verifier
    assert "+" not in verifier
    assert "/" not in verifier
    decoded = base64.urlsafe_b64decode(verifier + "==")
    assert len(decoded) == 32


def test_generate_pkce_challenge_matches_verifier():
    pkce = generate_pkce()
    expected = (
        base64.urlsafe_b64encode(
            hashlib.sha256(pkce["verifier"].encode("utf-8")).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    assert pkce["challenge"] == expected


def test_generate_pkce_produces_unique_verifiers():
    verifiers = {generate_pkce()["verifier"] for _ in range(10)}
    assert len(verifiers) == 10
