import csv
from pathlib import Path

from call_list import append_to_call_list, dedupe_key


def test_dedupe_key_phone_company_contact():
    a = {
        "contact_phone": "+358 40 672 6783",
        "company_name": "Mecmetal Oy",
        "contact_name": "Jani Toropainen",
    }
    b = {
        "contact_phone": "0406726783",
        "company_name": "mecmetal oy",
        "contact_name": "jani toropainen",
    }
    assert dedupe_key(a) == dedupe_key(b)


def test_append_dedupes_existing(tmp_path: Path):
    path = tmp_path / "call_list.csv"
    rows = [
        {
            "company_name": "Adven Oy",
            "contact_name": "Juha Elo",
            "contact_title": "SVP",
            "contact_phone": "+358 40 594 4755",
            "phone_type": "mobile",
            "industry": "energy",
            "company_url": "https://www.adven.com",
            "contact_brief": "Brief",
            "verified_for_outreach": "yes",
            "phone_verification_status": "person_page_match",
        },
        {
            "company_name": "Adven Oy",
            "contact_name": "Juha Elo",
            "contact_title": "SVP",
            "contact_phone": "+358 40 594 4755",
            "phone_type": "mobile",
            "industry": "energy",
            "company_url": "https://www.adven.com",
            "contact_brief": "Brief duplicate",
            "verified_for_outreach": "yes",
            "phone_verification_status": "person_page_match",
        },
        {
            "company_name": "Ponsse Oyj",
            "contact_name": "Juho Nummela",
            "contact_title": "CEO",
            "contact_phone": "+358 400 495 690",
            "phone_type": "mobile",
            "industry": "manufacturing",
            "company_url": "https://www.ponsse.com",
            "contact_brief": "CEO brief",
            "verified_for_outreach": "yes",
            "phone_verification_status": "person_page_match",
        },
    ]
    added1, skipped1 = append_to_call_list(rows[:2], path, source_run_id="run1")
    added2, skipped2 = append_to_call_list(rows, path, source_run_id="run2")

    assert added1 == 1
    assert skipped1 == 1
    assert added2 == 1
    assert skipped2 == 2

    with path.open(encoding="utf-8", newline="") as f:
        saved = list(csv.DictReader(f))
    assert len(saved) == 2
    assert saved[0]["source_run_id"] == "run1"
    assert saved[1]["source_run_id"] == "run2"
