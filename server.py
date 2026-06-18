#!/usr/bin/env python3
"""
SMS System - Simple 2-message flow with PostgreSQL persistence
Message 1: Blast opener (sent via blast.py)
Message 2: If they respond positively, ask if they're free for a call
Then Gavin takes over
"""

from flask import Flask, request, jsonify, render_template_string
from twilio.rest import Client
import json
import os
import psycopg2
import psycopg2.extras
import threading
import time
from datetime import datetime

app = Flask(__name__)

# ============================================================
# CONFIGURATION
# ============================================================
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "ACadf86f354db213cd7b5769d3816f6c84")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "3ca7119907fa08da43fb5696bf5d035c")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "+12362432623")
DATABASE_URL       = os.environ.get("DATABASE_URL", "")

# ============================================================
# STAGES
# ============================================================
STAGES = [
    ("NEW",        "New"),
    ("REPLIED",    "Replied"),
    ("CALL_SENT",  "Call Asked"),
    ("SCHEDULED",  "Scheduled"),
    ("TAKEOVER",   "Your Turn"),
    ("DEAD",       "Not Interested"),
]

STAGE_COLORS = {
    "NEW":       "#555",
    "REPLIED":   "#1a6fbd",
    "CALL_SENT": "#b57a00",
    "SCHEDULED": "#0a7a8a",
    "TAKEOVER":  "#fb923c",
    "DEAD":      "#444",
}

# ============================================================
# DATABASE
# ============================================================
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    try:
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
                conversation JSONB DEFAULT '[]',
                notes JSONB DEFAULT '[]'
            )
        """)
        cur.execute("ALTER TABLE prospects ADD COLUMN IF NOT EXISTS notes JSONB DEFAULT '[]'")
        conn.commit()
        cur.close()
        conn.close()
        print("Database initialized")
    except Exception as e:
        print(f"DB init error: {e}")

def get_all_prospects():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM prospects")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = {}
        for row in rows:
            result[row['phone']] = {
                'name': row['name'],
                'stage': row['stage'],
                'last_message': row['last_message'],
                'updated_at': row['updated_at'],
                'takeover': row['takeover'],
                'conversation': row['conversation'] if row['conversation'] else [],
                'notes': row['notes'] if row['notes'] else []
            }
        return result
    except Exception as e:
        print(f"DB get error: {e}")
        return {}

def get_prospect(phone):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM prospects WHERE phone = %s", (phone,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return {
                'name': row['name'],
                'stage': row['stage'],
                'last_message': row['last_message'],
                'updated_at': row['updated_at'],
                'takeover': row['takeover'],
                'conversation': row['conversation'] if row['conversation'] else [],
                'notes': row['notes'] if row['notes'] else []
            }
        return None
    except Exception as e:
        print(f"DB get prospect error: {e}")
        return None

def save_prospect(phone, data):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO prospects (phone, name, stage, last_message, updated_at, takeover, conversation, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (phone) DO UPDATE SET
                name = EXCLUDED.name,
                stage = EXCLUDED.stage,
                last_message = EXCLUDED.last_message,
                updated_at = EXCLUDED.updated_at,
                takeover = EXCLUDED.takeover,
                conversation = EXCLUDED.conversation,
                notes = EXCLUDED.notes
        """, (
            phone,
            data.get('name', 'Unknown'),
            data.get('stage', 'NEW'),
            data.get('last_message', ''),
            data.get('updated_at', ''),
            data.get('takeover', False),
            json.dumps(data.get('conversation', [])),
            json.dumps(data.get('notes', []))
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB save error: {e}")

# ============================================================
# POSITIVE / NEGATIVE DETECTION
# ============================================================
POSITIVE_KEYWORDS = [
    "yes", "yeah", "yep", "yup", "sure", "absolutely", "definitely",
    "interested", "still", "of course", "sounds good", "ok", "okay",
    "i am", "i do", "for sure", "lets do it", "let's do it", "correct",
    "totally", "100", "drop", "dropshipping", "want to", "please"
]

NEGATIVE_KEYWORDS = [
    "no", "nope", "not interested", "stop", "unsubscribe", "remove",
    "dont contact", "don't contact", "leave me alone", "not anymore",
    "changed my mind", "never mind", "nevermind", "cancel"
]

def is_negative(message):
    msg = message.lower().strip()
    for word in NEGATIVE_KEYWORDS:
        if word in msg:
            return True
    return False

def is_positive(message):
    if is_negative(message):
        return False
    msg = message.lower().strip()
    for word in POSITIVE_KEYWORDS:
        if word in msg:
            return True
    if len(msg) < 20:
        return True
    return True

# ============================================================
# SECOND MESSAGE
# ============================================================
CALL_MESSAGE = "Awesome! Are you free for a quick call today so we can get you set up?"

# ============================================================
# TWILIO WEBHOOK
# ============================================================
@app.route("/sms", methods=["POST"])
def sms_reply():
    incoming_msg = request.form.get("Body", "").strip()
    from_number  = request.form.get("From", "").strip()
    print(f"\nINBOUND {from_number}: {incoming_msg}")

    prospect = get_prospect(from_number)
    if not prospect:
        prospect = {
            "name": "Unknown",
            "stage": "NEW",
            "last_message": "",
            "updated_at": "",
            "takeover": False,
            "conversation": []
        }

    prospect["last_message"] = incoming_msg
    prospect["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    prospect["conversation"].append({
        "role": "lead",
        "content": incoming_msg,
        "time": datetime.now().strftime("%H:%M")
    })

    # Takeover active — just log
    if prospect.get("takeover"):
        save_prospect(from_number, prospect)
        print(f"Takeover active for {from_number}")
        return ('', 204)

    # Already sent call message — switch to takeover
    if prospect["stage"] in ["CALL_SENT", "SCHEDULED", "TAKEOVER"]:
        prospect["takeover"] = True
        prospect["stage"] = "TAKEOVER"
        save_prospect(from_number, prospect)
        return ('', 204)

    # Dead lead — ignore
    if prospect["stage"] == "DEAD":
        save_prospect(from_number, prospect)
        return ('', 204)

    # Negative reply
    if is_negative(incoming_msg):
        prospect["stage"] = "DEAD"
        save_prospect(from_number, prospect)
        print(f"{from_number} not interested")
        return ('', 204)

    # Positive reply — send call message with 10 second delay
    if is_positive(incoming_msg):
        prospect["stage"] = "CALL_SENT"
        prospect["conversation"].append({
            "role": "gavin",
            "content": CALL_MESSAGE,
            "time": datetime.now().strftime("%H:%M")
        })
        save_prospect(from_number, prospect)

        def send_delayed(phone):
            time.sleep(10)
            try:
                client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                client.messages.create(
                    body=CALL_MESSAGE,
                    from_=TWILIO_FROM_NUMBER,
                    to=phone
                )
                print(f"Sent call message to {phone}")
            except Exception as e:
                print(f"Error sending message: {e}")

        threading.Thread(target=send_delayed, args=(from_number,), daemon=True).start()
        return ('', 204)

    save_prospect(from_number, prospect)
    return ('', 204)

# ============================================================
# DASHBOARD
# ============================================================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pipeline</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: #F7F7F5;
    color: #111827;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Inter', sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* ── HEADER ── */
  header {
    background: #FFFFFF;
    border-bottom: 1px solid #E5E5E3;
    padding: 0 20px;
    height: 56px;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  }

  .logo {
    font-size: 14px;
    font-weight: 700;
    color: #111827;
    letter-spacing: -0.2px;
    display: flex;
    align-items: center;
    gap: 7px;
    flex-shrink: 0;
  }

  .logo-icon {
    width: 26px;
    height: 26px;
    background: #2563EB;
    border-radius: 7px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .logo-icon svg { display: block; }

  /* ── SEARCH ── */
  .search-wrap {
    position: relative;
    flex-shrink: 0;
  }

  .search-icon {
    position: absolute;
    left: 9px;
    top: 50%;
    transform: translateY(-50%);
    color: #9CA3AF;
    pointer-events: none;
    display: flex;
  }

  .search-input {
    background: #F9FAFB;
    border: 1px solid #E5E7EB;
    border-radius: 7px;
    padding: 7px 10px 7px 30px;
    font-size: 13px;
    color: #111827;
    width: 190px;
    outline: none;
    transition: border-color 0.15s, box-shadow 0.15s, background 0.15s;
    font-family: inherit;
  }

  .search-input:focus {
    border-color: #93C5FD;
    box-shadow: 0 0 0 3px rgba(37,99,235,0.08);
    background: #FFFFFF;
  }

  .search-input::placeholder { color: #C4C9D4; }

  /* ── HEADER RIGHT ── */
  .header-right {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-left: auto;
  }

  /* ── NOTIFICATION BADGE ── */
  .notif-badge {
    display: none;
    align-items: center;
    gap: 5px;
    background: #FEF2F2;
    border: 1px solid #FECACA;
    color: #DC2626;
    font-size: 11px;
    font-weight: 700;
    padding: 4px 10px;
    border-radius: 20px;
    cursor: pointer;
    white-space: nowrap;
    transition: background 0.15s;
    flex-shrink: 0;
  }

  .notif-badge.visible { display: flex; }

  .notif-badge:hover {
    background: #FEE2E2;
  }

  .notif-dot {
    width: 6px;
    height: 6px;
    background: #EF4444;
    border-radius: 50%;
    flex-shrink: 0;
    animation: pulse 1.8s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  /* ── STATS ── */
  .stats {
    display: flex;
    align-items: center;
    gap: 2px;
    background: #F3F4F6;
    border: 1px solid #E5E7EB;
    border-radius: 9px;
    padding: 3px;
    flex-shrink: 0;
  }

  .stat {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 5px 13px;
    border-radius: 7px;
    cursor: default;
  }

  .stat-n {
    font-size: 16px;
    font-weight: 700;
    line-height: 1;
    color: #374151;
  }

  .stat-l {
    font-size: 11px;
    color: #9CA3AF;
    font-weight: 500;
    white-space: nowrap;
  }

  .stat-divider {
    width: 1px;
    height: 20px;
    background: #E5E7EB;
    flex-shrink: 0;
  }

  .stat.highlighted {
    background: #FFFFFF;
    box-shadow: 0 1px 3px rgba(0,0,0,0.07);
  }

  .stat.highlighted .stat-n { color: #2563EB; }

  .refresh-btn {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    color: #6B7280;
    padding: 7px 13px;
    border-radius: 7px;
    cursor: pointer;
    font-size: 12px;
    font-weight: 500;
    transition: all 0.15s;
    display: flex;
    align-items: center;
    gap: 5px;
    white-space: nowrap;
    flex-shrink: 0;
  }

  .refresh-btn:hover {
    background: #F9FAFB;
    color: #374151;
    border-color: #D1D5DB;
  }

  /* ── BOARD ── */
  .board {
    display: flex;
    flex: 1;
    overflow-x: auto;
    overflow-y: hidden;
    padding: 14px 16px;
    gap: 10px;
  }

  .board::-webkit-scrollbar { height: 5px; }
  .board::-webkit-scrollbar-track { background: transparent; }
  .board::-webkit-scrollbar-thumb { background: #D1D5DB; border-radius: 3px; }

  /* ── COLUMN ── */
  .col {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 12px;
    display: flex;
    flex-direction: column;
    min-width: 215px;
    flex: 1;
    max-width: 285px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    overflow: hidden;
  }

  .col-head {
    padding: 11px 13px 10px;
    display: flex;
    align-items: center;
    gap: 7px;
    border-bottom: 1px solid #F3F4F6;
    flex-shrink: 0;
    background: #FAFAFA;
  }

  .stage-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .col-title {
    font-size: 12px;
    font-weight: 600;
    color: #374151;
    flex: 1;
    letter-spacing: 0.01em;
  }

  .col-count {
    font-size: 11px;
    font-weight: 600;
    background: #F3F4F6;
    color: #9CA3AF;
    padding: 2px 7px;
    border-radius: 20px;
    min-width: 22px;
    text-align: center;
  }

  .col-body {
    flex: 1;
    overflow-y: auto;
    padding: 9px;
    display: flex;
    flex-direction: column;
    gap: 7px;
  }

  .col-body::-webkit-scrollbar { width: 3px; }
  .col-body::-webkit-scrollbar-thumb { background: #E5E7EB; border-radius: 2px; }

  .col-body.drag-over {
    background: #EFF6FF;
    outline: 2px dashed #BFDBFE;
    outline-offset: -3px;
    border-radius: 6px;
  }

  .empty-col {
    color: #D1D5DB;
    font-size: 12px;
    text-align: center;
    padding: 28px 10px;
  }

  /* ── CARD ── */
  .card {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 9px;
    padding: 10px 12px;
    cursor: pointer;
    transition: box-shadow 0.15s, border-color 0.15s, transform 0.1s;
    user-select: none;
  }

  .card:hover {
    box-shadow: 0 2px 8px rgba(0,0,0,0.09);
    border-color: #C7CDD6;
  }

  .card.dragging {
    opacity: 0.45;
    transform: scale(0.96) rotate(0.8deg);
    cursor: grabbing;
    box-shadow: 0 8px 24px rgba(0,0,0,0.13);
  }

  .card-name {
    font-size: 13px;
    font-weight: 600;
    color: #111827;
    margin-bottom: 2px;
  }

  .card-phone {
    font-size: 11px;
    color: #9CA3AF;
    margin-bottom: 6px;
    font-family: 'SF Mono', 'Fira Code', 'Menlo', monospace;
    letter-spacing: 0.02em;
  }

  .card-last {
    font-size: 12px;
    color: #6B7280;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.4;
  }

  .card-time {
    font-size: 10px;
    color: #D1D5DB;
    margin-top: 6px;
  }

  /* ── PANEL BACKDROP ── */
  .panel-backdrop {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(17,24,39,0.16);
    z-index: 40;
  }

  .panel-backdrop.open { display: block; }

  /* ── SIDE PANEL ── */
  .side-panel {
    position: fixed;
    top: 0;
    right: 0;
    width: 390px;
    max-width: 96vw;
    height: 100vh;
    background: #FFFFFF;
    border-left: 1px solid #E5E5E3;
    box-shadow: -6px 0 28px rgba(0,0,0,0.1);
    z-index: 50;
    display: flex;
    flex-direction: column;
    transform: translateX(100%);
    transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
  }

  .side-panel.open { transform: translateX(0); }

  .panel-header {
    padding: 16px 18px 13px;
    border-bottom: 1px solid #F3F4F6;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    flex-shrink: 0;
    background: #FAFAFA;
  }

  .panel-name {
    font-size: 16px;
    font-weight: 700;
    color: #111827;
    line-height: 1.2;
  }

  .panel-phone {
    font-size: 11px;
    color: #9CA3AF;
    margin-top: 3px;
    font-family: 'SF Mono', 'Fira Code', 'Menlo', monospace;
    letter-spacing: 0.02em;
  }

  .panel-close {
    background: #F3F4F6;
    border: none;
    color: #6B7280;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    cursor: pointer;
    font-size: 17px;
    line-height: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.15s;
    flex-shrink: 0;
    margin-top: 1px;
  }

  .panel-close:hover { background: #E5E7EB; color: #374151; }

  .panel-meta {
    padding: 9px 18px;
    border-bottom: 1px solid #F3F4F6;
    display: flex;
    gap: 7px;
    flex-wrap: wrap;
    flex-shrink: 0;
    align-items: center;
  }

  .stage-badge {
    font-size: 11px;
    font-weight: 600;
    padding: 3px 9px 3px 7px;
    border-radius: 20px;
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }

  .badge-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .meta-time { font-size: 11px; color: #9CA3AF; }

  /* ── CONVERSATION ── */
  .convo {
    flex: 1;
    min-height: 80px;
    overflow-y: auto;
    padding: 14px 18px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    background: #F9FAFB;
  }

  .convo::-webkit-scrollbar { width: 4px; }
  .convo::-webkit-scrollbar-thumb { background: #E5E7EB; border-radius: 2px; }

  .bubble-wrap { display: flex; flex-direction: column; }
  .bubble-wrap.out { align-items: flex-end; }

  .bubble-label {
    font-size: 10px;
    color: #9CA3AF;
    margin-bottom: 4px;
    letter-spacing: 0.02em;
  }

  .bubble {
    max-width: 82%;
    padding: 9px 13px;
    border-radius: 13px;
    font-size: 13px;
    line-height: 1.55;
  }

  .bubble.in {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    color: #111827;
    border-bottom-left-radius: 3px;
  }

  .bubble.out {
    background: #2563EB;
    color: #FFFFFF;
    border-bottom-right-radius: 3px;
  }

  .empty-convo {
    color: #D1D5DB;
    font-size: 13px;
    text-align: center;
    padding: 40px 20px;
  }

  /* ── NOTES SECTION ── */
  .notes-section {
    border-top: 1px solid #F3F4F6;
    flex-shrink: 0;
    background: #FFFFFF;
  }

  .notes-toggle-row {
    padding: 8px 18px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    cursor: pointer;
    user-select: none;
  }

  .notes-toggle-row:hover { background: #FAFAFA; }

  .notes-label {
    font-size: 11px;
    font-weight: 600;
    color: #9CA3AF;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    display: flex;
    align-items: center;
    gap: 5px;
  }

  .notes-count-badge {
    background: #F3F4F6;
    color: #6B7280;
    font-size: 10px;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 10px;
  }

  .notes-chevron {
    font-size: 9px;
    color: #C4C9D4;
    transition: transform 0.2s;
  }

  .notes-chevron.open { transform: rotate(180deg); }

  .notes-body {
    padding: 0 18px 12px;
    display: none;
  }

  .notes-body.open { display: block; }

  .notes-list {
    max-height: 110px;
    overflow-y: auto;
    margin-bottom: 9px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .notes-list:empty::after {
    content: 'No notes yet';
    display: block;
    font-size: 11px;
    color: #D1D5DB;
    text-align: center;
    padding: 8px 0;
    font-style: italic;
  }

  .notes-list::-webkit-scrollbar { width: 3px; }
  .notes-list::-webkit-scrollbar-thumb { background: #E5E7EB; }

  .note-item {
    background: #FFFBEB;
    border: 1px solid #FDE68A;
    border-radius: 7px;
    padding: 7px 10px;
  }

  .note-text { font-size: 12px; color: #78350F; line-height: 1.45; }
  .note-time { font-size: 10px; color: #B45309; margin-top: 3px; }

  .notes-compose {
    display: flex;
    gap: 7px;
    align-items: flex-end;
  }

  .notes-compose textarea {
    flex: 1;
    background: #F9FAFB;
    border: 1px solid #E5E7EB;
    border-radius: 7px;
    color: #111827;
    padding: 7px 10px;
    font-size: 12px;
    resize: none;
    height: 52px;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s, box-shadow 0.15s;
    line-height: 1.4;
  }

  .notes-compose textarea::placeholder { color: #C4C9D4; }

  .notes-compose textarea:focus {
    border-color: #93C5FD;
    background: #FFFFFF;
    box-shadow: 0 0 0 3px rgba(37,99,235,0.08);
  }

  .btn-save-note {
    background: #F9FAFB;
    border: 1px solid #E5E7EB;
    color: #374151;
    padding: 7px 12px;
    border-radius: 7px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
    flex-shrink: 0;
    align-self: flex-end;
    height: 52px;
  }

  .btn-save-note:hover {
    background: #EFF6FF;
    border-color: #BFDBFE;
    color: #1D4ED8;
  }

  .btn-save-note:disabled {
    background: #F3F4F6;
    color: #C4C9D4;
    cursor: not-allowed;
    border-color: #E5E7EB;
  }

  /* ── PANEL ACTIONS ── */
  .panel-actions {
    padding: 11px 18px;
    border-top: 1px solid #F3F4F6;
    display: flex;
    gap: 7px;
    flex-wrap: wrap;
    flex-shrink: 0;
    background: #FFFFFF;
  }

  .btn {
    padding: 7px 13px;
    border-radius: 7px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid;
    transition: all 0.15s;
    white-space: nowrap;
    line-height: 1.4;
  }

  .btn-primary { background: #2563EB; color: #FFFFFF; border-color: #2563EB; }
  .btn-primary:hover { background: #1D4ED8; border-color: #1D4ED8; }

  .btn-ghost { background: #FFFFFF; color: #374151; border-color: #E5E7EB; }
  .btn-ghost:hover { background: #F9FAFB; border-color: #D1D5DB; color: #111827; }

  /* ── STAGE PICKER ── */
  .stage-picker {
    padding: 10px 18px;
    border-top: 1px solid #F3F4F6;
    display: none;
    flex-wrap: wrap;
    gap: 6px;
    flex-shrink: 0;
    background: #FAFAFA;
  }

  .stage-picker.open { display: flex; }

  .stage-opt {
    font-size: 11px;
    font-weight: 500;
    padding: 5px 11px 5px 9px;
    border-radius: 20px;
    cursor: pointer;
    border: 1px solid #E5E7EB;
    background: #FFFFFF;
    color: #374151;
    transition: all 0.12s;
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }

  .stage-opt:hover { background: #EFF6FF; border-color: #BFDBFE; color: #1D4ED8; }

  .stage-opt-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }

  /* ── REPLY BOX ── */
  .reply-box {
    padding: 11px 18px 13px;
    border-top: 1px solid #F3F4F6;
    display: none;
    flex-shrink: 0;
    background: #FFFFFF;
  }

  .reply-box.open { display: block; }

  .reply-box textarea {
    width: 100%;
    background: #F9FAFB;
    border: 1px solid #E5E7EB;
    border-radius: 8px;
    color: #111827;
    padding: 9px 12px;
    font-size: 13px;
    resize: none;
    height: 70px;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s, box-shadow 0.15s;
    line-height: 1.5;
  }

  .reply-box textarea::placeholder { color: #C4C9D4; }

  .reply-box textarea:focus {
    border-color: #93C5FD;
    background: #FFFFFF;
    box-shadow: 0 0 0 3px rgba(37,99,235,0.08);
  }

  .reply-foot { display: flex; justify-content: flex-end; margin-top: 7px; }

  .btn-send {
    background: #2563EB;
    color: #FFFFFF;
    padding: 7px 18px;
    border-radius: 7px;
    font-weight: 600;
    font-size: 13px;
    border: none;
    cursor: pointer;
    transition: background 0.15s;
  }

  .btn-send:hover { background: #1D4ED8; }
  .btn-send:disabled { background: #E5E7EB; color: #9CA3AF; cursor: not-allowed; }
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-icon">
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
        <rect x="1" y="1" width="5" height="5" rx="1.5" fill="white" opacity="0.9"/>
        <rect x="8" y="1" width="5" height="5" rx="1.5" fill="white" opacity="0.6"/>
        <rect x="1" y="8" width="5" height="5" rx="1.5" fill="white" opacity="0.6"/>
        <rect x="8" y="8" width="5" height="5" rx="1.5" fill="white" opacity="0.3"/>
      </svg>
    </div>
    Pipeline
  </div>

  <div class="search-wrap">
    <span class="search-icon">
      <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
        <circle cx="5.5" cy="5.5" r="4" stroke="currentColor" stroke-width="1.4"/>
        <path d="M8.5 8.5L11 11" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
      </svg>
    </span>
    <input class="search-input" type="text" id="search-input" placeholder="Search leads…" oninput="filterCards(this.value)" autocomplete="off">
  </div>

  <div class="header-right">
    <span class="notif-badge" id="notif-badge" onclick="clearBadge()">
      <span class="notif-dot"></span>
      <span id="notif-text"></span>
    </span>

    <div class="stats">
      <div class="stat">
        <div class="stat-n" id="s-total">0</div>
        <div class="stat-l">Total leads</div>
      </div>
      <div class="stat-divider"></div>
      <div class="stat highlighted">
        <div class="stat-n" id="s-replied">0</div>
        <div class="stat-l">Replied</div>
      </div>
      <div class="stat-divider"></div>
      <div class="stat highlighted">
        <div class="stat-n" id="s-yours">0</div>
        <div class="stat-l">Your turn</div>
      </div>
    </div>

    <button class="refresh-btn" onclick="manualLoad()">
      <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
        <path d="M9.5 5.5A4 4 0 1 1 5.5 1.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
        <path d="M5.5 1.5L7.5 3.5L5.5 3.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      Refresh
    </button>
  </div>
</header>

<div class="board" id="board"></div>

<div class="panel-backdrop" id="panel-backdrop" onclick="closePanel()"></div>

<div class="side-panel" id="side-panel">
  <div class="panel-header">
    <div>
      <div class="panel-name" id="p-name"></div>
      <div class="panel-phone" id="p-phone"></div>
    </div>
    <button class="panel-close" onclick="closePanel()">&#x2715;</button>
  </div>
  <div class="panel-meta" id="p-meta"></div>
  <div class="convo" id="p-convo"></div>

  <div class="notes-section">
    <div class="notes-toggle-row" onclick="toggleNotesPanel()">
      <span class="notes-label">
        Notes
        <span class="notes-count-badge" id="notes-count">0</span>
      </span>
      <span class="notes-chevron" id="notes-chevron">&#9650;</span>
    </div>
    <div class="notes-body open" id="notes-body">
      <div class="notes-list" id="notes-list"></div>
      <div class="notes-compose">
        <textarea id="note-text" placeholder="Add a private note…"></textarea>
        <button class="btn-save-note" id="btn-save-note" onclick="saveNote()">Save</button>
      </div>
    </div>
  </div>

  <div class="panel-actions">
    <button class="btn btn-primary" id="btn-take" onclick="toggleTakeover()">Take over</button>
    <button class="btn btn-ghost" onclick="togglePicker()">Move stage</button>
  </div>
  <div class="stage-picker" id="stage-picker"></div>
  <div class="reply-box" id="reply-box">
    <textarea id="reply-text" placeholder="Type your reply…"></textarea>
    <div class="reply-foot">
      <button class="btn-send" id="btn-send" onclick="sendMsg()">Send</button>
    </div>
  </div>
</div>

<script>
const STAGES = [
  ["NEW",       "New",            "#94A3B8"],
  ["REPLIED",   "Replied",        "#3B82F6"],
  ["CALL_SENT", "Call Asked",     "#F59E0B"],
  ["SCHEDULED", "Scheduled",      "#10B981"],
  ["TAKEOVER",  "Your Turn",      "#F97316"],
  ["DEAD",      "Not Interested", "#9CA3AF"],
];

let data = {};
let cur = null;
let notesPanelOpen = true;

// ── SEARCH ──────────────────────────────────────────────────
function filterCards(query) {
  const q = query.toLowerCase().trim();
  document.querySelectorAll('.col').forEach(col => {
    let visible = 0;
    col.querySelectorAll('.card').forEach(card => {
      const name  = (card.querySelector('.card-name')?.textContent  || '').toLowerCase();
      const phone = (card.querySelector('.card-phone')?.textContent || '').toLowerCase();
      const match = !q || name.includes(q) || phone.includes(q);
      card.style.display = match ? '' : 'none';
      if (match) visible++;
    });
    const countEl = col.querySelector('.col-count');
    if (countEl) countEl.textContent = visible;
  });
}

// ── NOTIFICATIONS ────────────────────────────────────────────
function parseDBTime(s) {
  if (!s) return 0;
  try { return new Date(s.replace(' ', 'T') + ':00').getTime(); } catch(e) { return 0; }
}

function getLastVisit() {
  return parseInt(localStorage.getItem('pipelineLastVisit') || '0', 10);
}

function setLastVisit() {
  localStorage.setItem('pipelineLastVisit', Date.now().toString());
}

function countNewReplies() {
  const lastVisit = getLastVisit();
  if (!lastVisit) return 0;
  const active = new Set(['REPLIED','CALL_SENT','SCHEDULED','TAKEOVER']);
  let n = 0;
  for (const p of Object.values(data)) {
    if (active.has(p.stage) && parseDBTime(p.updated_at) > lastVisit) n++;
  }
  return n;
}

function updateBadge(n) {
  const badge = document.getElementById('notif-badge');
  const text  = document.getElementById('notif-text');
  if (n > 0) {
    text.textContent = n === 1 ? '1 new reply' : n + ' new replies';
    badge.classList.add('visible');
    document.title = '(' + n + ') Pipeline';
  } else {
    badge.classList.remove('visible');
    document.title = 'Pipeline';
  }
}

function clearBadge() {
  setLastVisit();
  updateBadge(0);
}

function manualLoad() {
  setLastVisit();
  updateBadge(0);
  load(true);
}

// ── CORE DATA ────────────────────────────────────────────────
async function load(isManual) {
  const res  = await fetch('/api/prospects');
  const json = await res.json();
  data = json.prospects || {};
  document.getElementById('s-total').textContent   = json.total    || 0;
  document.getElementById('s-replied').textContent = json.replied  || 0;
  document.getElementById('s-yours').textContent   = json.takeover || 0;
  renderBoard();
  if (cur && data[cur]) renderPanel(cur);
  if (!isManual) updateBadge(countNewReplies());
  const q = document.getElementById('search-input').value;
  if (q) filterCards(q);
}

function renderBoard() {
  const board = document.getElementById('board');
  board.innerHTML = '';
  STAGES.forEach(([code, label, color]) => {
    const leads = Object.entries(data).filter(([,p]) => p.stage === code);
    const col   = document.createElement('div');
    col.className   = 'col';
    col.dataset.stage = code;
    col.innerHTML = `
      <div class="col-head">
        <div class="stage-dot" style="background:${color}"></div>
        <span class="col-title">${label}</span>
        <span class="col-count">${leads.length}</span>
      </div>
      <div class="col-body" id="body-${code}"
           ondragover="onDragOver(event)"
           ondragleave="onDragLeave(event)"
           ondrop="onDrop(event,'${code}')">
        ${leads.length === 0 ? '<div class="empty-col">No leads</div>' : ''}
      </div>
    `;
    board.appendChild(col);
    const body = col.querySelector('.col-body');
    leads.forEach(([phone, p]) => {
      const card = document.createElement('div');
      card.className  = 'card';
      card.draggable  = true;
      card.dataset.phone = phone;
      card.onclick    = () => openPanel(phone);
      card.ondragstart = (e) => { e.dataTransfer.setData('phone', phone); card.classList.add('dragging'); };
      card.ondragend   = () => card.classList.remove('dragging');
      const last = p.last_message ? p.last_message.substring(0,52)+(p.last_message.length>52?'…':'') : '';
      card.innerHTML = `
        <div class="card-name">${p.name || 'Unknown'}</div>
        <div class="card-phone">${phone}</div>
        ${last ? `<div class="card-last">${last}</div>` : ''}
        ${p.updated_at ? `<div class="card-time">${p.updated_at}</div>` : ''}
      `;
      body.appendChild(card);
    });
  });
}

function onDragOver(e) { e.preventDefault(); e.currentTarget.classList.add('drag-over'); }
function onDragLeave(e) { e.currentTarget.classList.remove('drag-over'); }
async function onDrop(e, stage) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  const phone = e.dataTransfer.getData('phone');
  if (!phone || !data[phone]) return;
  await fetch('/api/set_stage', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({phone, stage}) });
  await load(true);
}

// ── PANEL ────────────────────────────────────────────────────
function openPanel(phone) {
  cur = phone;
  renderPanel(phone);
  document.getElementById('panel-backdrop').classList.add('open');
  document.getElementById('side-panel').classList.add('open');
}

function renderPanel(phone) {
  const p = data[phone];
  if (!p) return;
  document.getElementById('p-name').textContent  = p.name || 'Unknown';
  document.getElementById('p-phone').textContent = phone;

  const stageInfo = STAGES.find(([c]) => c === p.stage) || STAGES[0];
  const hex = stageInfo[2];
  document.getElementById('p-meta').innerHTML = `
    <span class="stage-badge" style="background:${hex}18;color:${hex};border:1px solid ${hex}38">
      <span class="badge-dot" style="background:${hex}"></span>${stageInfo[1]}
    </span>
    ${p.updated_at ? `<span class="meta-time">${p.updated_at}</span>` : ''}
  `;

  const convo = document.getElementById('p-convo');
  convo.innerHTML = '';
  const msgs = p.conversation || [];
  if (!msgs.length) { convo.innerHTML = '<div class="empty-convo">No messages yet</div>'; }
  msgs.forEach(msg => {
    const isIn = msg.role === 'lead';
    const wrap = document.createElement('div');
    wrap.className = 'bubble-wrap' + (isIn ? '' : ' out');
    wrap.innerHTML = `<div class="bubble-label">${isIn ? (p.name || 'Lead') : 'You'}${msg.time ? ' · '+msg.time : ''}</div><div class="bubble ${isIn?'in':'out'}">${msg.content}</div>`;
    convo.appendChild(wrap);
  });
  setTimeout(() => convo.scrollTop = convo.scrollHeight, 50);

  renderNotes(p.notes || []);

  const btnTake  = document.getElementById('btn-take');
  const replyBox = document.getElementById('reply-box');
  if (p.takeover) {
    btnTake.textContent = 'Resume auto';
    btnTake.className   = 'btn btn-ghost';
    replyBox.classList.add('open');
  } else {
    btnTake.textContent = 'Take over';
    btnTake.className   = 'btn btn-primary';
    replyBox.classList.remove('open');
  }

  const picker = document.getElementById('stage-picker');
  picker.classList.remove('open');
  picker.innerHTML = STAGES.map(([code, label, color]) =>
    `<span class="stage-opt" onclick="setStage('${code}')"><span class="stage-opt-dot" style="background:${color}"></span>${label}</span>`
  ).join('');
}

function closePanel() {
  document.getElementById('panel-backdrop').classList.remove('open');
  document.getElementById('side-panel').classList.remove('open');
  document.getElementById('stage-picker').classList.remove('open');
  cur = null;
}

async function toggleTakeover() {
  if (!cur) return;
  const p = data[cur];
  await fetch('/api/takeover', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({phone:cur, takeover:!p.takeover}) });
  await load(true);
}

async function sendMsg() {
  if (!cur) return;
  const text = document.getElementById('reply-text').value.trim();
  if (!text) return;
  const btn = document.getElementById('btn-send');
  btn.disabled = true; btn.textContent = 'Sending…';
  const res  = await fetch('/api/send', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({phone:cur, message:text}) });
  const json = await res.json();
  if (json.ok) { document.getElementById('reply-text').value = ''; await load(true); }
  else alert('Failed: ' + (json.error || 'unknown error'));
  btn.disabled = false; btn.textContent = 'Send';
}

function togglePicker() { document.getElementById('stage-picker').classList.toggle('open'); }

async function setStage(stage) {
  if (!cur) return;
  await fetch('/api/set_stage', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({phone:cur, stage}) });
  document.getElementById('stage-picker').classList.remove('open');
  await load(true);
}

// ── NOTES ────────────────────────────────────────────────────
function renderNotes(notes) {
  const list  = document.getElementById('notes-list');
  const count = document.getElementById('notes-count');
  list.innerHTML = '';
  count.textContent = notes.length;
  notes.forEach(n => {
    const item = document.createElement('div');
    item.className = 'note-item';
    item.innerHTML = `<div class="note-text">${n.text.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</div><div class="note-time">${n.time}</div>`;
    list.appendChild(item);
  });
  setTimeout(() => list.scrollTop = list.scrollHeight, 30);
}

async function saveNote() {
  if (!cur) return;
  const text = document.getElementById('note-text').value.trim();
  if (!text) return;
  const btn = document.getElementById('btn-save-note');
  btn.disabled = true; btn.textContent = 'Saving…';
  const res  = await fetch('/api/save_note', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({phone:cur, note:text}) });
  const json = await res.json();
  if (json.ok) { document.getElementById('note-text').value = ''; await load(true); }
  else alert('Failed to save note: ' + (json.error || 'unknown error'));
  btn.disabled = false; btn.textContent = 'Save';
}

function toggleNotesPanel() {
  notesPanelOpen = !notesPanelOpen;
  document.getElementById('notes-body').classList.toggle('open', notesPanelOpen);
  const chevron = document.getElementById('notes-chevron');
  chevron.classList.toggle('open', notesPanelOpen);
}

// ── INIT ─────────────────────────────────────────────────────
document.addEventListener('keydown', e => { if (e.key === 'Escape') closePanel(); });

setLastVisit();
load(true);
setInterval(() => load(false), 30000);
</script>
</body>
</html>
"""

@app.route("/dashboard")
def dashboard():
    import json as _json
    stages_json = _json.dumps(STAGES)
    colors_json = _json.dumps(STAGE_COLORS)
    html = DASHBOARD_HTML.replace("STAGES_JSON", stages_json).replace("COLORS_JSON", colors_json)
    return html

@app.route("/api/prospects")
def api_prospects():
    all_p = get_all_prospects()
    return jsonify({
        "total": len(all_p),
        "replied": sum(1 for p in all_p.values() if p.get("stage") in ["REPLIED","CALL_SENT"]),
        "takeover": sum(1 for p in all_p.values() if p.get("stage") == "TAKEOVER"),
        "prospects": all_p
    })

@app.route("/api/takeover", methods=["POST"])
def api_takeover():
    data = request.json
    phone = data.get("phone")
    takeover = data.get("takeover", True)
    prospect = get_prospect(phone)
    if prospect:
        prospect["takeover"] = takeover
        save_prospect(phone, prospect)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404

@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.json
    phone = data.get("phone")
    message = data.get("message", "").strip()
    if not phone or not message:
        return jsonify({"ok": False, "error": "Missing phone or message"}), 400
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(body=message, from_=TWILIO_FROM_NUMBER, to=phone)
        prospect = get_prospect(phone)
        if prospect:
            prospect["conversation"].append({"role": "gavin", "content": message, "time": datetime.now().strftime("%H:%M")})
            prospect["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            save_prospect(phone, prospect)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/set_stage", methods=["POST"])
def api_set_stage():
    data = request.json
    phone = data.get("phone")
    stage = data.get("stage")
    prospect = get_prospect(phone)
    if prospect:
        prospect["stage"] = stage
        save_prospect(phone, prospect)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404

@app.route("/api/save_note", methods=["POST"])
def api_save_note():
    data = request.json
    phone = data.get("phone")
    note_text = data.get("note", "").strip()
    if not phone or not note_text:
        return jsonify({"ok": False, "error": "Missing phone or note"}), 400
    prospect = get_prospect(phone)
    if not prospect:
        return jsonify({"ok": False, "error": "Prospect not found"}), 404
    notes = prospect.get("notes", [])
    notes.append({"text": note_text, "time": datetime.now().strftime("%Y-%m-%d %H:%M")})
    prospect["notes"] = notes
    save_prospect(phone, prospect)
    return jsonify({"ok": True})

@app.route("/")
def health():
    return f"SMS System running | <a href='/dashboard'>Dashboard</a>", 200

# Initialize DB on startup
init_db()

if __name__ == "__main__":
    print("Starting server on http://localhost:5000")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
