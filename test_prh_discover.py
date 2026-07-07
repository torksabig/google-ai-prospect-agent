"""Tests for PRH/YTJ discovery parsing."""

from prh_discover import _parse_company


def test_parse_company_accepts_nested_business_id():
    row = _parse_company(
        {
            "businessId": {"value": "0100411-8", "registrationDate": "1978-03-15"},
            "names": [{"name": "Deplano Ky", "type": "1"}],
            "addresses": [
                {
                    "type": 2,
                    "postOffices": [
                        {"city": "KARIS", "languageCode": "2"},
                        {"city": "KARJAA", "languageCode": "1"},
                    ],
                }
            ],
            "businessLines": [{"code": "71121"}],
        }
    )

    assert row is not None
    assert row["company_name"] == "Deplano Ky"
    assert row["business_id"] == "0100411-8"
    assert row["city"] == "KARIS"
    assert row["industry"] == "71121"
