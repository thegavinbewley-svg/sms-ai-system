#!/usr/bin/env python3
"""
SMS Blaster - Reads leads from CSV, sends opener messages, registers in database
"""

import csv
import time
import json
import os
import psycopg2
import psycopg2.extras
from twilio.rest import Client
from datetime import datetime

# ============================================================
# CONFIGURATION — update DATABASE_URL with your Railway Postgres URL
# ============================================================
ACCOUNT_SID    = "ACadf86f354db213cd7b5769d3816f6c84"
AUTH_TOKEN     = "3ca7119907fa08da43fb5696bf5d035c"
FROM_NUMBER    = "+12362432623"
DATABASE_URL   = "postgresql://postgres:UmxLgFpvtBDtQxFxZJPkluhdHpSLoDok@thomas.proxy.rlwy.net:42443/railway"

CSV_FILE       = "leads.csv"
FIRST_NAME_COL = "first_name"
PHONE_COL      = "primary_phone"
DELAY_SECONDS  = 2

# ============================================================
# OPENER MESSAGE
# ============================================================
def build_opener(first_name):
    return (
        f"Hey {first_name}, this is Gavin I work with Nathan Nazareth. "
        f"You were interested in starting dropshipping with us — is that still the case?"
    )

# ============================================================
# DATABASE
# ============================================================
def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prospects (
            phone TEXT PRIMARY KEY,
            name TEXT DEFAULT 'Unknown',
            stage TEXT DEFAULT 'NEW',
            last_message TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            takeover BOOLEAN DEFAULT FALSE,
            conversation JSONB DEFAULT '[]'
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def register_lead(phone, name, opener_message):
    try:
        conn = get_db()
        cur = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conversation = json.dumps([{
            "role": "gavin",
            "content": opener_message,
            "time": datetime.now().strftime("%H:%M")
        }])
        cur.execute("""
            INSERT INTO prospects (phone, name, stage, last_message, updated_at, takeover, conversation)
            VALUES (%s, %s, 'NEW', %s, %s, FALSE, %s)
            ON CONFLICT (phone) DO UPDATE SET
                name = EXCLUDED.name,
                updated_at = EXCLUDED.updated_at
        """, (phone, name, opener_message, now, conversation))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"  DB error for {name}: {e}")

# ============================================================
# SEND BLAST
# ============================================================
def send_blast():
    print("Connecting to database...")
    try:
        init_db()
        print("Database ready.")
    except Exception as e:
        print(f"Database connection failed: {e}")
        print("Will still send texts but won't show on dashboard.")

    client = Client(ACCOUNT_SID, AUTH_TOKEN)

    with open(CSV_FILE, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        leads = list(reader)

    print(f"Loaded {len(leads)} leads")
    print("-" * 50)

    sent = 0
    failed = 0
    skipped = 0

    for lead in leads:
        first_name = lead.get(FIRST_NAME_COL, "").strip()
        phone      = lead.get(PHONE_COL, "").strip()

        if not phone or not first_name:
            skipped += 1
            continue

        if not phone.startswith("+"):
            phone = "+1" + phone.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")

        message_body = build_opener(first_name)

        try:
            msg = client.messages.create(
                body=message_body,
                from_=FROM_NUMBER,
                to=phone
            )
            register_lead(phone, first_name, message_body)
            print(f"Sent to {first_name} ({phone})")
            sent += 1
        except Exception as e:
            print(f"FAILED {first_name} ({phone}): {e}")
            failed += 1

        time.sleep(DELAY_SECONDS)

    print("-" * 50)
    print(f"Sent: {sent}  |  Failed: {failed}  |  Skipped: {skipped}")
    print("Check your dashboard to see all leads!")

if __name__ == "__main__":
    send_blast()
