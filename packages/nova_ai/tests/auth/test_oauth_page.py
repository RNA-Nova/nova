"""OAuth 回调页面模板测试。"""

from nova_ai.auth.oauth_page import oauth_error_html, oauth_success_html


def test_oauth_success_html_contains_title_and_message():
    html = oauth_success_html("Login complete")
    assert "<title>Authentication successful</title>" in html
    assert "Authentication successful" in html
    assert "Login complete" in html


def test_oauth_error_html_contains_title_message_and_details():
    html = oauth_error_html("Login failed", "invalid scope")
    assert "<title>Authentication failed</title>" in html
    assert "Authentication failed" in html
    assert "Login failed" in html
    assert "invalid scope" in html
    assert '<div class="details">invalid scope</div>' in html


def test_oauth_page_escapes_xss_payloads():
    payload = "<script>alert('xss')</script>"
    html = oauth_error_html(payload, payload)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_oauth_success_html_has_no_details_when_not_provided():
    html = oauth_success_html("All good")
    assert 'class="details"' not in html
