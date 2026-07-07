from hermes_search import (
    HermesSearchSpec,
    build_search_queries,
    dedupe_rows,
)


def test_build_search_queries_finnish_templates():
    spec = HermesSearchSpec(
        industries=["energy", "proptech"],
        titles=["CTO", "CEO"],
        queries_per_combo=2,
    )
    queries = build_search_queries(spec)
    assert any('site:.fi "CTO" "puhelin" "energia"' in q for q in queries)
    assert any('site:.fi "toimitusjohtaja" "puhelin"' in q for q in queries)
    assert len(queries) == len(set(queries))


def test_dedupe_by_domain_name_phone():
    rows = [
        {
            "company_domain": "mecmetal.fi",
            "contact_name": "Jani Toropainen",
            "contact_phone": "+358 40 672 6783",
        },
        {
            "company_domain": "mecmetal.fi",
            "contact_name": "Jani Toropainen",
            "contact_phone": "+358 40 672 6783",
        },
        {
            "company_domain": "ponsse.com",
            "contact_name": "Juho Nummela",
            "contact_phone": "+358 400 495 690",
        },
    ]
    out = dedupe_rows(rows)
    assert len(out) == 2
