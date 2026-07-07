"""Tests for google-ai-prospect-agent helpers."""

import json
from tempfile import TemporaryDirectory
from pathlib import Path

from cli import _load_hermes_json
from search_agent import (
    _normalize_company,
    _parse_json,
    expand_companies_to_rows,
    filter_companies,
)
from notion_pipeline import build_prospect_notes, rows_to_pipeline


def test_expand_dual_contacts():
    companies = [
        {
            "company_name": "Acme Oy",
            "company_domain": "acme.fi",
            "contacts": [
                {"contact_name": "A", "contact_phone": "+358 50 1", "phone_type": "mobile"},
                {"contact_name": "B", "contact_phone": "+358 9 123", "phone_type": "switchboard"},
            ],
        }
    ]
    rows = expand_companies_to_rows(companies, contacts_per_company=2)
    assert len(rows) == 2
    assert rows[0]["company_name"] == "Acme Oy"
    assert rows[1]["contact_name"] == "B"


def test_filter_dual_contact_company():
    co = {
        "company_name": "Acme",
        "company_domain": "acme.fi",
        "company_url": "https://acme.fi",
        "contacts": [
            {"contact_name": "A", "contact_phone": "+358 50 123 4567", "phone_type": "mobile"},
        ],
    }
    rows = filter_companies([co], require_phone=True, contacts_per_company=2)
    assert len(rows) == 1


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


def test_build_prospect_notes_uses_brief_first_format():
    notes = build_prospect_notes(
        {
            "company_name": "Mecmetal Oy",
            "company_url": "https://www.mecmetal.fi/",
            "contact_name": "Jani Toropainen",
            "contact_title": "Engineering Manager",
            "contact_phone": "+358 40 672 6783",
            "phone_type": "mobile",
            "contact_brief": "Jani Toropainen on Mecmetal Oy:n Engineering Manager.",
            "phone_verification_status": "person_page_match",
            "phone_verification_reason": "Mobile on official contact page",
        }
    )

    assert notes.startswith("Jani Toropainen on Mecmetal Oy:n Engineering Manager.\n\n")
    assert (
        "**Jani Toropainen** — Engineering Manager\n\n"
        "Website: https://www.mecmetal.fi/\n\n"
        "Puhelin: +358 40 672 6783 (mobile)\n\n"
        "Verify: person_page_match — Mobile on official contact page"
    ) in notes


def test_load_hermes_json_flattens_companies_contacts():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "hermes.json"
        path.write_text(
            json.dumps(
                {
                    "companies": [
                        {
                            "company_name": "Mecmetal Oy",
                            "company_url": "https://www.mecmetal.fi/",
                            "contacts": [
                                {
                                    "contact_name": "Erik Hiltunen",
                                    "contact_title": "Engineering Manager",
                                    "contact_phone": "+358 40 672 6783",
                                    "phone_type": "mobile",
                                    "evidence_urls": ["https://www.mecmetal.fi/fi/yhteystiedot/"],
                                }
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        rows = _load_hermes_json(path)

    assert len(rows) == 1
    assert rows[0]["company_domain"] == "mecmetal.fi"
    assert rows[0]["contact_name"] == "Erik Hiltunen"
    assert rows[0]["evidence_urls"] == "https://www.mecmetal.fi/fi/yhteystiedot/"


def test_rows_to_pipeline_exports_only_outreach_ready_named_people():
    rows = [
        {
            "company_name": "Mecmetal Oy",
            "company_domain": "mecmetal.fi",
            "contact_name": "Erik Hiltunen",
            "contact_title": "Engineering Manager",
            "contact_phone": "+358 40 672 6783",
            "phone_verification_status": "person_page_match",
            "verified_for_outreach": "yes",
            "company_url": "https://www.mecmetal.fi/",
        },
        {
            "company_name": "Pori Energia Oy",
            "company_domain": "porienergia.fi",
            "contact_name": "Asiakaspalvelu",
            "contact_title": "CTO",
            "contact_phone": "09 2315 0465",
            "phone_verification_status": "company_switchboard_only",
            "verified_for_outreach": "no",
            "company_url": "https://porienergia.fi/",
        },
        {
            "company_name": "Haining Engineering",
            "company_domain": "haining.fi",
            "contact_name": "Haining Engineering",
            "contact_title": "CTO",
            "contact_phone": "+358 40 111 2222",
            "phone_verification_status": "direct_claim_unverified",
            "verified_for_outreach": "no",
            "company_url": "https://haining.fi/",
        },
    ]

    pipeline, skipped = rows_to_pipeline(rows)

    assert len(pipeline) == 1
    assert skipped == 2
    assert pipeline[0]["contact_name"] == "Erik Hiltunen"
