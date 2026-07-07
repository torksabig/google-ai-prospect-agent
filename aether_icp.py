"""Default ICP profile for Aether Applied outbound (web-search prospecting).

Used by `cli.py search --preset aether` and as the standard when Gemini/OpenRouter
grounded search runs for Aether Applied leads.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AetherICP:
    """Filters for company discovery + callable decision-makers."""

    country: str = "Finland"
    revenue: str = "1M - 5B EUR"
    industry: str = (
        "teollisuusautomaatio, energiatekniikka, konepajat, LVI-automaatio, "
        "puunjalostus, kemianteollisuus, ohjelmisto teollisuudelle, metalliteollisuus"
    )
    titles: str = (
        "CTO, teknologiajohtaja, tutkimusjohtaja, Head of R&D, innovaatiojohtaja, "
        "kehitysjohtaja, VP Engineering, tekninen johtaja"
    )
    secondary_titles: str = (
        "toimitusjohtaja, CEO, COO, liiketoimintajohtaja, myyntijohtaja, "
        "Head of Operations, plant manager"
    )
    company_limit: int = 200
    contacts_per_company: int = 2
    batch_size: int = 8
    max_batches: int = 30
    batch_pause: int = 5
    rotate_industries: bool = True
    require_phone: bool = True  # direct preferred; switchboard OK as fallback
    direct_phone_first: bool = False
    websites_only: bool = False

    industry_rotation: tuple[str, ...] = (
        "teollisuusautomaatio",
        "energiatekniikka ja kaukolämpö",
        "kone- ja laitevalmistus",
        "LVI-automaatio ja talotekniikka",
        "puolijohde ja elektroniikka",
        "ohjelmisto teollisuudelle",
        "metalliteollisuus ja konepajat",
        "puunjalostus ja metsäteollisuus",
        "kemianteollisuus ja prosessiteollisuus",
        "logistiikka ja materiaalinkäsittely",
    )

    product_context: str = (
        "Aether Applied sells B2B industrial / automation solutions. "
        "Target buyers own innovation, R&D, engineering, or plant technology decisions."
    )


AETHER_DEFAULTS = AetherICP()
