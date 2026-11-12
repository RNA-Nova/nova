"""Ark 在售状态源的解析边界测试。"""

import pytest
from nova_catalog.sources.ark import parse_ark_status


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": {}}, []])
def test_ark_parser_rejects_missing_or_malformed_data(payload):
    assert parse_ark_status(payload) is None


def test_ark_parser_accepts_empty_list_and_extracts_bad_status():
    assert parse_ark_status({"data": []}) == {}
    assert parse_ark_status(
        {
            "data": [
                {"id": "a", "status": "Shutdown"},
                {"id": "b", "status": "Running"},
            ]
        }
    ) == {"a": "Shutdown"}


@pytest.mark.parametrize("model_id", [None, "", 42])
def test_ark_bad_status_without_valid_id_is_rejected(model_id):
    entry = {"status": "Shutdown"}
    if model_id is not None:
        entry["id"] = model_id
    assert parse_ark_status({"data": [entry]}) is None
