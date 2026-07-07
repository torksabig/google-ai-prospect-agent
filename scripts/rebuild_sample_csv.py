#!/usr/bin/env python3
"""Rebuild sample prospect CSV from last known 29-lead export (for verify/demo)."""
from __future__ import annotations

import csv
from pathlib import Path

ROWS = [
    ("Raute Oyj","raute.com","https://www.raute.com","Mika Saariaho","President and CEO","+358 40 154 9393","mobile","https://www.raute.com/investors/ir-contacts","1.0"),
    ("Ponsse Oyj","ponsse.com","https://www.ponsse.com","Juho Nummela","Toimitusjohtaja","+358 400 495 690","mobile","https://www.ponsse.com/fi/sijoittajat/sijoittajayhteydet","1.0"),
    ("Granlund Oy","granlund.fi","https://www.granlund.fi","Heikki Ihasalo","Department Director, Innovation","+358 40 820 9623","mobile","https://www.granlund.fi/en/about-us/contact-information/helsinki-head-office/","1.0"),
    ("Vincit Oyj","vincit.com","https://www.vincit.com","Julius Manni","Toimitusjohtaja","+358 50 424 3932","mobile","https://www.vincit.com/investors/management-team","1.0"),
    ("Etteplan Oyj","etteplan.com","https://www.etteplan.com","Juha Näkki","President and CEO","+358 10 307 2077","direct","https://www.etteplan.com/investors/investor-contacts","1.0"),
    ("Micropower Oy","micropower.fi","https://www.micropower.fi","Harry Lilja","Toimitusjohtaja (CEO)","+358 40 725 9549","direct","https://www.micropower.fi/contact-us-at-micropower-group/","1.0"),
    ("Kotek Factory Service Oy","kotekservice.com","https://www.kotekservice.com","Mikko Koskinen","Toimitusjohtaja","+358 40 515 7747","direct","https://www.kotekservice.com/contact/","1.0"),
    ("RE Group (RE-Suunnittelu Oy)","regroup.fi","https://www.regroup.fi","Jukka Heikkinen","Toimitusjohtaja (CEO)","+358 50 346 4365","direct","https://www.regroup.fi/contact-details/","1.0"),
    ("Nordec Group Corporation","nordec.com","https://nordec.com","Kalle Luoto","Toimitusjohtaja (CEO)","+358 50 552 9682","direct","https://nordec.com/about-us/management-team/","1.0"),
    ("IQM Quantum Computers","me.iqm.com","https://me.iqm.com","Dr. Jan Goetz","Toimitusjohtaja (CEO)","", "unknown","","0.9"),
    ("Meyer Turku Oy","meyerturku.com","https://www.meyerturku.com","Casimir Lindholm","Toimitusjohtaja","","unknown","","0.8"),
    ("VTT Technical Research Centre of Finland Ltd","vtt.fi","https://www.vtt.fi","Kalle Härkki","Toimitusjohtaja","","unknown","","0.8"),
    ("Kemppi Oy","kemppi.com","https://www.kemppi.com","Kalle Suurpää","Toimitusjohtaja","","unknown","","0.5"),
    ("Outokumpu Oyj","outokumpu.com","https://www.outokumpu.com","Kati ter Horst","Toimitusjohtaja","+358 9 4211","switchboard","https://www.outokumpu.com/investors/ir-contacts","0.9"),
    ("Neste Oyj","neste.com","https://www.neste.com","Heikki Malinen","Toimitusjohtaja","+358 10 45811","switchboard","https://www.neste.com/fi/yhteystiedot","0.9"),
    ("KONE Oyj","kone.com","https://www.kone.com","Philippe Delorme","Toimitusjohtaja","+358 204 751","switchboard","https://www.kone.com/fi/yhteystiedot/","0.9"),
    ("Valmet Oyj","valmet.com","https://www.valmet.com","Thomas Hinnerskov","Toimitusjohtaja","+358 10 672 0000","switchboard","https://www.valmet.com/fi/yhteystiedot","0.8"),
    ("Helen Oy","helen.fi","https://www.helen.fi","Olli Sirkka","Toimitusjohtaja","+358 9 6171","switchboard","https://www.helen.fi/yhteystiedot","0.8"),
]

FIELDS = [
    "company_name","company_domain","company_url","contact_name","contact_title",
    "contact_phone","phone_type","phone_source_url","confidence",
]


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "output" / "sample_web_prospects_29.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(ROWS)
    print(out)


if __name__ == "__main__":
    main()
