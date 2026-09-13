"""授权输入解析测试（自 nova_ai 迁入——解析归接线层 acquisition）。"""

from nova_harness.config.auth.acquisition import parse_authorization_input


def test_parse_authorization_input_from_url():
    parsed = parse_authorization_input(
        "http://localhost:1455/auth/callback?code=abc&state=xyz"
    )
    assert parsed == {"code": "abc", "state": "xyz"}


def test_parse_authorization_input_from_hash():
    parsed = parse_authorization_input("abc#xyz")
    assert parsed == {"code": "abc", "state": "xyz"}


def test_parse_authorization_input_from_query_string():
    parsed = parse_authorization_input("code=abc&state=xyz")
    assert parsed == {"code": "abc", "state": "xyz"}


def test_parse_authorization_input_plain_code():
    parsed = parse_authorization_input("abc")
    assert parsed == {"code": "abc", "state": None}
