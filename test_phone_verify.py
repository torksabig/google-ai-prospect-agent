from phone_verify import verify_phone_row, verified_for_outreach_status


def test_no_phone():
    r = verify_phone_row({"contact_name": "A", "contact_phone": ""})
    assert r["phone_verification_status"] == "no_phone_found"
    assert r["verified_for_outreach"] == "no"


def test_switchboard():
    r = verify_phone_row({
        "contact_name": "CEO",
        "contact_phone": "+358 10 1234",
        "phone_type": "switchboard",
        "phone_source_url": "https://co.fi/yhteystiedot",
    })
    assert r["phone_verification_status"] == "company_switchboard_only"


def test_person_page_match():
    r = verify_phone_row({
        "contact_name": "Mika Saariaho",
        "contact_phone": "+358 40 154 9393",
        "phone_type": "mobile",
        "phone_source_url": "https://www.raute.com/investors/ir-contacts",
        "evidence_urls": "https://www.raute.com/fi/sijoittajat/hallinnointi/toimitusjohtaja",
        "confidence": "1.0",
    })
    assert r["phone_verification_status"] == "person_page_match"
    assert verified_for_outreach_status(r["phone_verification_status"])


def test_direct_unverified():
    r = verify_phone_row({
        "contact_name": "Someone",
        "contact_phone": "+358 50 111 2222",
        "phone_type": "mobile",
        "phone_source_url": "https://example.com/",
    })
    assert r["phone_verification_status"] == "direct_claim_unverified"
    assert r["verified_for_outreach"] == "no"


def test_dial_confirmed_override():
    r = verify_phone_row({"phone_verification_status": "dial_confirmed"})
    assert r["verified_for_outreach"] == "yes"
