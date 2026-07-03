"""Tests for google-ai-prospect-agent helpers."""

from search_agent import _parse_json, filter_companies, _normalize_company


def test_parse_json_with_fences():
    raw = '```json\n{"companies": [], "search_notes": "ok"}\n```'
    data = _parse_json(raw)
    assert data is not None
    assert data["search_notes"] == "ok"


def test_filter_direct_phone_only():
    rows = [
        {"company_name": "A", "contact_phone": "+358 50 123 4567", "phone_type": "mobile"},
        {"company_name": "B", "contact_phone": "", "phone_type": "switchboard"},
        {"company_name": "C", "contact_phone": "+358 9 123456", "phone_type": "switchboard"},
    ]
    direct = filter_companies(rows, direct_phone_first=True)
    assert len(direct) == 1
    assert direct[0]["company_name"] == "A"

    all_phones = filter_companies(rows, direct_phone_first=False)
    assert len(all_phones) == 2


def test_parse_json_embedded():
    raw = 'Here is data:\n```json\n{"companies": [{"company_name": "X", "contact_phone": "+358 50 1", "phone_type": "mobile"}], "search_notes": "ok"}\n```\n'
    data = _parse_json(raw)
    assert data is not None
    assert len(data["companies"]) == 1


def test_normalize_infers_mobile():
    row = _normalize_company({"contact_phone": "+358501234567", "phone_type": "unknown"})
    assert row["phone_type"] == "mobile"
