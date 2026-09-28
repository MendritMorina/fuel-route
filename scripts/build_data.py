"""Build compact, offline station/city indexes from the supplied CSV and US Census places."""

import csv
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "fuel-prices-for-be-assessment.csv"
CENSUS = ROOT / "census-places-2025.zip"
DEST = ROOT / "api" / "data.json"
VALID_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)


def normalize(value):
    value = re.sub(r"\bsaint\b", "st", value, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def main():
    places = {}
    with zipfile.ZipFile(CENSUS) as archive:
        with archive.open("2025_Gaz_place_national.txt") as file:
            for row in csv.DictReader(
                (line.decode("utf-8-sig") for line in file), delimiter="|"
            ):
                state = row["USPS"]
                if state not in VALID_STATES:
                    continue
                name = re.sub(r" \(balance\)$", "", row["NAME"], flags=re.I)
                name = re.sub(
                    r" (city|town|village|CDP|borough|municipality)$",
                    "",
                    name,
                    flags=re.I,
                )
                key = f"{normalize(name)},{state}"
                places.setdefault(
                    key, [float(row["INTPTLAT"]), float(row["INTPTLONG"])]
                )

    stations = []
    missing = Counter()
    seen = set()
    with SOURCE.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            state = row["State"].strip().upper()
            city = row["City"].strip()
            point = places.get(f"{normalize(city)},{state}")
            if not point:
                missing[(city, state)] += 1
                continue
            try:
                price = float(row["Retail Price"])
            except (ValueError, TypeError):
                continue
            if price <= 0:
                continue
            station_id = row["OPIS Truckstop ID"].strip()
            key = (station_id, normalize(row["Address"]), normalize(city), state, price)
            if key in seen:
                continue
            seen.add(key)
            stations.append(
                {
                    "id": station_id,
                    "name": row["Truckstop Name"].strip(),
                    "address": row["Address"].strip(),
                    "city": city,
                    "state": state,
                    "price": price,
                    "lat": point[0],
                    "lon": point[1],
                }
            )

    DEST.write_text(
        json.dumps({"places": places, "stations": stations}, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"{len(places)} places, {len(stations)} stations, {sum(missing.values())} unmatched rows"
    )
    print("Top unmatched:", missing.most_common(15))


if __name__ == "__main__":
    main()
