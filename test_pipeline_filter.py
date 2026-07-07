from pipeline_filter import (
    filter_outreach_ready,
    is_invalid_phone,
    looks_like_company_contact_name,
    is_title_only_name,
    outreach_reject_reason,
)


def test_reject_empty_name():
    row = {"contact_name": "", "contact_phone": "+358 40 111 2222"}
    assert outreach_reject_reason(row) == "empty_contact_name"


def test_reject_title_only_name():
    assert is_title_only_name("cto")
    assert is_title_only_name("CEO")
    assert not is_title_only_name("Mika Saariaho")
    row = {"contact_name": "cto", "contact_phone": "+358 40 111 2222"}
    assert outreach_reject_reason(row) == "title_only_name"


def test_reject_invalid_phones():
    assert is_invalid_phone("000000000")
    assert is_invalid_phone("0 0 128 128")
    assert not is_invalid_phone("+358 40 154 9393")


def test_keep_name_and_valid_phone():
    row = {
        "contact_name": "Mika Saariaho",
        "contact_phone": "+358 40 154 9393",
    }
    assert outreach_reject_reason(row) is None


def test_reject_company_like_contact_name():
    assert looks_like_company_contact_name("Haining Engineering", "Haining Engineering")
    assert looks_like_company_contact_name("Pori Energia", "Pori Energia Oy")
    row = {
        "company_name": "WSP Finland Oy",
        "company_domain": "wsp.fi",
        "contact_name": "Finland Oy",
        "contact_phone": "+358 40 154 9393",
    }
    assert outreach_reject_reason(row) == "company_like_contact_name"


def test_filter_counts():
    rows = [
        {"contact_name": "", "contact_phone": "0 0 128 128"},
        {"contact_name": "cto", "contact_phone": "+358 40 111 2222"},
        {"contact_name": "Juho Nummela", "contact_phone": "+358 400 495 690"},
        {"contact_name": "Heikki Ihasalo", "contact_phone": ""},
    ]
    result = filter_outreach_ready(rows)
    assert result.kept_count == 1
    assert result.removed_count == 3
    assert result.reason_counts["empty_contact_name"] == 1
    assert result.reason_counts["title_only_name"] == 1
    assert result.reason_counts["empty_contact_phone"] == 1


def test_verification_required_when_present():
    rows = [
        {
            "contact_name": "A B",
            "contact_phone": "+358 40 111 2222",
            "verified_for_outreach": "no",
            "phone_verification_status": "direct_claim_unverified",
        }
    ]
    result = filter_outreach_ready(rows)
    assert result.kept_count == 0
    assert result.reason_counts["verification_failed"] == 1


def test_verification_switchboard_allowed():
    rows = [
        {
            "contact_name": "A B",
            "contact_phone": "+358 10 1234567",
            "verified_for_outreach": "no",
            "phone_verification_status": "company_switchboard_only",
        }
    ]
    result = filter_outreach_ready(rows)
    assert result.kept_count == 0
    assert result.reason_counts["verification_failed"] == 1
