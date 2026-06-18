#!/usr/bin/env python3
"""
One-time script: backfill 'Unknown' names in the prospects table from leads.csv.
Matches on phone number (normalised to digits-only) and sets name = first_name.
"""

import csv
import os
import re
import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:UmxLgFpvtBDtQxFxZJPkluhdHpSLoDok@thomas.proxy.rlwy.net:42443/railway",
)
CSV_PATH = os.path.join(os.path.dirname(__file__), "leads.csv")


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def load_csv(path: str) -> dict:
    """Return {normalised_digits: first_name} from the CSV."""
    mapping = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            phone = row.get("primary_phone", "").strip()
            name = row.get("first_name", "").strip()
            if phone and name:
                mapping[digits(phone)] = name
    return mapping


def main():
    name_map = load_csv(CSV_PATH)
    print(f"Loaded {len(name_map)} phone→name entries from CSV")

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("SELECT phone FROM prospects WHERE name = 'Unknown'")
    unknown_rows = [row[0] for row in cur.fetchall()]
    print(f"Found {len(unknown_rows)} prospects with name='Unknown'")

    updated = 0
    skipped = 0
    for phone in unknown_rows:
        key = digits(phone)
        first_name = name_map.get(key)
        if first_name:
            cur.execute(
                "UPDATE prospects SET name = %s WHERE phone = %s",
                (first_name, phone),
            )
            print(f"  Updated {phone} → {first_name}")
            updated += 1
        else:
            print(f"  No match in CSV for {phone}, skipping")
            skipped += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"\nDone. {updated} updated, {skipped} skipped (no CSV match).")


if __name__ == "__main__":
    main()
