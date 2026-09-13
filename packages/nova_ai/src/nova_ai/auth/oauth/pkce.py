"""PKCE 工具。

对齐 TypeScript ``src/auth/oauth/pkce.ts``：生成 code verifier 与 challenge。
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class PkceCodes:
    """PKCE code verifier 与 challenge（S256）——不可变值对象（规则 5）。"""

    verifier: str
    challenge: str


def generate_pkce() -> PkceCodes:
    """生成 PKCE verifier 与 challenge（S256）。"""
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")

    challenge_bytes = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(challenge_bytes).rstrip(b"=").decode("ascii")

    return PkceCodes(verifier=verifier, challenge=challenge)


__all__ = ["PkceCodes", "generate_pkce"]
