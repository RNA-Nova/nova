"""Secret 文本清洗（出口统一件——codex ``redact_secrets`` 对位）。

用法纪律：**在出口调用**（落盘 / 出栈 / 呈现给客户端前），不在生产者
内部调用——新功能自动被覆盖，不依赖每个作者自觉。词表按"低误报优先"
设计（token 字符集 + 最小长度），误报对抗用例见 ``tests/test_sanitize.py``。
"""

import re
from typing import List

# 密钥模式词表（codex 四条对位 + nova 增补两条；token 字符集 + 长度约束压误报）
_SECRET_PATTERNS: List[re.Pattern] = [
    # HTTP Bearer token（"Bearer of good news" 这类自然语言不误杀：
    # token 必须是连续 token 字符且 ≥10）
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{10,}"),
    # OpenAI / Anthropic 系 sk- 前缀（sk- / sk-proj- / sk-ant- 等）
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    # AWS Access Key ID（AKIA + 16 位大写字母数字）
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # GitHub token 族（ghp_/gho_/ghu_/ghs_/ghr_/github_pat_——Copilot 凭据源
    # 走 GH_TOKEN/GITHUB_TOKEN；代理/网关 401 回显请求头是常见恶习）
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})"),
    # api_key / token 族键名赋值形（= 或 :，键与值均可选引号——覆盖 JSON
    # 形态 "access_token": "..."；键名单含 OAuth 响应全家族 +
    # client_secret/secret_key）
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token"
        r"|authorization[_-]?code|code[_-]?verifier|client[_-]?secret"
        r"|secret[_-]?key)[\"']?\s*[=:]\s*[\"']?"
        r"[A-Za-z0-9._~+/=-]{8,}"
    ),
    # JWT（"eyJ" 是 `{"` 的 base64url——OAuth access/id token 的主力形态；
    # 三段式约束压误报，eyJ 开头的自然语言文本极罕见）
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.?[A-Za-z0-9_-]*"),
]

REDACTED_SECRET = "[REDACTED_SECRET]"
"""替换后留下的占位符（与 codex 同词）。"""


def redact_secrets(text: str) -> str:
    """把文本中已知密钥模式替换为 ``[REDACTED_SECRET]``（纯函数）。"""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED_SECRET, text)
    return text


__all__ = ["REDACTED_SECRET", "redact_secrets"]
