#!/usr/bin/env python3
"""
SMS Blaster - Reads leads from CSV and sends personalized opener messages via Twilio
Also registers each lead in prospects.json so the AI server can track them from the start
"""

import csv
import time
import json
import os
from twilio.rest import Client

# ============================================================
# CONFIGURATION
# ============================================================
ACCOUNT_SID = "ACadf86f354db213cd7b5769d3816f6c84"
AUTH_TOKEN  = "bba89b16c02066e1cec0091ecbd100eb"
FROM_NUMBER = "+12362432623"

CSV_FILE   = "leads.csv"
DATA_FILE  = "prospects.json"

# Column names in your CSV (adjust if different)
FIRST_NAME_COL = "First Name"
PHONE_COL      = "Phone"

# Delay between messages in seconds (avoids carrier flagging)
DELAY_SECONDS = 2

# ============================================================
# OPENER MESSAGE
# ============================================================
def build_opener(first_name):
    return (
        f"Hey {first_name}, this is Gavin I work with Nathan Nazareth. "
        f"You were interested in starting dropshipping with us — is that still the case?"
    )

# ============================================================
# LOAD / SAVE PROSPECTS
# ============================================================
def load_prospects():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_prospects(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ============================================================
# SEND BLAST
# ============================================================
def send_blast():
    client = Client(ACCOUNT_SID, AUTH_TOKEN)
    prospects = load_prospects()

    with open(CSV_FILE, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        leads = list(reader)

    print(f"📋 Loaded {len(leads)} leads from {CSV_FILE}")
    print("─" * 50)

    sent = 0
    failed = 0

    for lead in leads:
        first_name = lead.get(FIRST_NAME_COL, "").strip()
        phone      = lead.get(PHONE_COL, "").strip()

        if not phone or not first_name:
            print(f"⚠️  Skipping row — missing name or phone: {lead}")
            failed += 1
            continue

        # Normalize phone number
        if not phone.startswith("+"):
            phone = "+1" + phone.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")

        message_body = build_opener(first_name)

        try:
            msg = client.messages.create(
                body=message_body,
                from_=FROM_NUMBER,
                to=phone
            )
            print(f"✅ Sent to {first_name} ({phone}) — SID: {msg.sid}")

            # Register in prospects.json so server can track them
            if phone not in prospects:
                prospects[phone] = {
                    "name": first_name,
                    "stage": "NEW",
                    "budget": "unknown",
                    "why": "unknown",
                    "last_message": "",
                    "updated_at": "",
                    "conversation": []
                }
            else:
                # Update name if already exists
                prospects[phone]["name"] = first_name

            sent += 1
        except Exception as e:
            print(f"❌ Failed to send to {first_name} ({phone}): {e}")
            failed += 1

        time.sleep(DELAY_SECONDS)

    save_prospects(prospects)
    print("─" * 50)
    print(f"✅ Sent: {sent}  |  ❌ Failed: {failed}")
    print(f"💾 Saved {len(prospects)} prospects to {DATA_FILE}")

if __name__ == "__main__":
    send_blast()
