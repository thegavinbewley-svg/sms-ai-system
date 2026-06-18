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
    ("EVAN",       "Handed to Evan"),
    ("TAKEOVER",   "Your Turn"),
    ("DEAD",       "Not Interested"),
    ("ARCHIVED",   "Archived"),
]

STAGE_COLORS = {
    "NEW":       "#94A3B8",
    "REPLIED":   "#3B82F6",
    "CALL_SENT": "#F59E0B",
    "SCHEDULED": "#10B981",
    "EVAN":      "#8B5CF6",
    "TAKEOVER":  "#F97316",
    "DEAD":      "#9CA3AF",
    "ARCHIVED":  "#6B7280",
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
        cur.execute("ALTER TABLE prospects ADD COLUMN IF NOT EXISTS call_time TEXT DEFAULT ''")
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
                'notes': row['notes'] if row['notes'] else [],
                'call_time': row['call_time'] if row['call_time'] else ''
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
                'notes': row['notes'] if row['notes'] else [],
                'call_time': row['call_time'] if row['call_time'] else ''
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
            INSERT INTO prospects (phone, name, stage, last_message, updated_at, takeover, conversation, notes, call_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (phone) DO UPDATE SET
                name = EXCLUDED.name,
                stage = EXCLUDED.stage,
                last_message = EXCLUDED.last_message,
                updated_at = EXCLUDED.updated_at,
                takeover = EXCLUDED.takeover,
                conversation = EXCLUDED.conversation,
                notes = EXCLUDED.notes,
                call_time = EXCLUDED.call_time
        """, (
            phone,
            data.get('name', 'Unknown'),
            data.get('stage', 'NEW'),
            data.get('last_message', ''),
            data.get('updated_at', ''),
            data.get('takeover', False),
            json.dumps(data.get('conversation', [])),
            json.dumps(data.get('notes', [])),
            data.get('call_time', '')
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
    padding: 0 16px;
    height: 56px;
    display: flex;
    align-items: center;
    gap: 10px;
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
  .search-wrap { position: relative; flex-shrink: 0; }

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
    font-size: 12px;
    color: #111827;
    width: 160px;
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

  /* ── DATE FILTER ── */
  .date-filter {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 7px;
    color: #374151;
    font-size: 12px;
    font-weight: 500;
    padding: 7px 26px 7px 10px;
    cursor: pointer;
    outline: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%239CA3AF' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 8px center;
    flex-shrink: 0;
    font-family: inherit;
    transition: border-color 0.15s;
  }

  .date-filter:hover { border-color: #D1D5DB; }

  /* ── ARCHIVE TOGGLE ── */
  .archive-toggle {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    color: #6B7280;
    padding: 7px 11px;
    border-radius: 7px;
    cursor: pointer;
    font-size: 12px;
    font-weight: 500;
    transition: all 0.15s;
    white-space: nowrap;
    flex-shrink: 0;
    font-family: inherit;
  }

  .archive-toggle:hover { background: #F9FAFB; color: #374151; border-color: #D1D5DB; }
  .archive-toggle.active { background: #F5F3FF; border-color: #DDD6FE; color: #7C3AED; }

  /* ── HEADER RIGHT ── */
  .header-right { display: flex; align-items: center; gap: 9px; margin-left: auto; }

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
    flex-shrink: 0;
  }

  .notif-badge.visible { display: flex; }
  .notif-badge:hover { background: #FEE2E2; }

  .notif-dot {
    width: 6px;
    height: 6px;
    background: #EF4444;
    border-radius: 50%;
    flex-shrink: 0;
    animation: npulse 1.8s ease-in-out infinite;
  }

  @keyframes npulse { 0%,100% { opacity:1; } 50% { opacity:0.35; } }

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

  .stat { display: flex; align-items: center; gap: 6px; padding: 5px 11px; border-radius: 7px; cursor: default; }
  .stat-n { font-size: 15px; font-weight: 700; line-height: 1; color: #374151; }
  .stat-l { font-size: 10px; color: #9CA3AF; font-weight: 500; white-space: nowrap; }
  .stat-divider { width: 1px; height: 18px; background: #E5E7EB; flex-shrink: 0; }
  .stat.highlighted { background: #FFFFFF; box-shadow: 0 1px 3px rgba(0,0,0,0.07); }
  .stat.highlighted .stat-n { color: #2563EB; }

  .refresh-btn {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    color: #6B7280;
    padding: 7px 12px;
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
    font-family: inherit;
  }

  .refresh-btn:hover { background: #F9FAFB; color: #374151; border-color: #D1D5DB; }

  /* ── BOARD ── */
  .board {
    display: flex;
    flex: 1;
    overflow-x: auto;
    overflow-y: hidden;
    padding: 14px 14px;
    gap: 9px;
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
    min-width: 200px;
    flex: 1;
    max-width: 270px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    overflow: hidden;
  }

  .col.col-archived { background: #FAFAFA; border-color: #E5E7EB; }

  .col-head {
    padding: 10px 12px 9px;
    display: flex;
    align-items: center;
    gap: 7px;
    border-bottom: 1px solid #F3F4F6;
    flex-shrink: 0;
    background: #FAFAFA;
  }

  .stage-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
  .col-title { font-size: 11px; font-weight: 600; color: #374151; flex: 1; letter-spacing: 0.01em; }
  .col-count { font-size: 11px; font-weight: 600; background: #F3F4F6; color: #9CA3AF; padding: 2px 7px; border-radius: 20px; min-width: 22px; text-align: center; }

  .col-body {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
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

  .empty-col { color: #D1D5DB; font-size: 12px; text-align: center; padding: 26px 10px; }

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

  .card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.09); border-color: #C7CDD6; }
  .card.dragging { opacity: 0.45; transform: scale(0.96) rotate(0.8deg); cursor: grabbing; box-shadow: 0 8px 24px rgba(0,0,0,0.13); }
  .card.unread { border-left: 3px solid #2563EB; padding-left: 10px; }

  .card-name { font-size: 13px; font-weight: 600; color: #111827; margin-bottom: 2px; display: flex; align-items: center; gap: 5px; }
  .card-name.unread { font-weight: 700; }

  .unread-dot {
    width: 7px;
    height: 7px;
    background: #2563EB;
    border-radius: 50%;
    flex-shrink: 0;
    display: inline-block;
  }

  .card-phone { font-size: 11px; color: #9CA3AF; margin-bottom: 5px; font-family: 'SF Mono','Fira Code','Menlo',monospace; letter-spacing: 0.02em; }
  .card-last { font-size: 12px; color: #6B7280; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.4; }
  .card-time { font-size: 10px; color: #D1D5DB; margin-top: 5px; }

  .card-call {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 10px;
    font-weight: 600;
    color: #2563EB;
    background: #EFF6FF;
    border: 1px solid #BFDBFE;
    border-radius: 4px;
    padding: 2px 6px;
    margin-top: 5px;
  }

  /* ── PANEL BACKDROP ── */
  .panel-backdrop { display: none; position: fixed; inset: 0; background: rgba(17,24,39,0.16); z-index: 40; }
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
    padding: 15px 18px 12px;
    border-bottom: 1px solid #F3F4F6;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    flex-shrink: 0;
    background: #FAFAFA;
  }

  .panel-name { font-size: 15px; font-weight: 700; color: #111827; line-height: 1.2; }
  .panel-phone { font-size: 11px; color: #9CA3AF; margin-top: 3px; font-family: 'SF Mono','Fira Code','Menlo',monospace; letter-spacing: 0.02em; }

  .panel-close {
    background: #F3F4F6;
    border: none;
    color: #6B7280;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    cursor: pointer;
    font-size: 17px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.15s;
    flex-shrink: 0;
  }

  .panel-close:hover { background: #E5E7EB; color: #374151; }

  .panel-meta {
    padding: 8px 18px;
    border-bottom: 1px solid #F3F4F6;
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    flex-shrink: 0;
    align-items: center;
  }

  .stage-badge { font-size: 11px; font-weight: 600; padding: 3px 9px 3px 7px; border-radius: 20px; display: inline-flex; align-items: center; gap: 5px; }
  .badge-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
  .meta-time { font-size: 11px; color: #9CA3AF; }

  .call-meta-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    font-weight: 600;
    color: #2563EB;
    background: #EFF6FF;
    border: 1px solid #BFDBFE;
    border-radius: 20px;
    padding: 3px 9px;
  }

  /* ── PANEL SCROLL AREA ── */
  .panel-scroll-area {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }

  .panel-scroll-area::-webkit-scrollbar { width: 4px; }
  .panel-scroll-area::-webkit-scrollbar-thumb { background: #E5E7EB; border-radius: 2px; }

  /* ── CONVERSATION ── */
  .convo {
    padding: 13px 18px;
    display: flex;
    flex-direction: column;
    gap: 11px;
    background: #F9FAFB;
    min-height: 120px;
  }

  .bubble-wrap { display: flex; flex-direction: column; }
  .bubble-wrap.out { align-items: flex-end; }
  .bubble-label { font-size: 10px; color: #9CA3AF; margin-bottom: 4px; letter-spacing: 0.02em; }
  .bubble { max-width: 82%; padding: 9px 13px; border-radius: 13px; font-size: 13px; line-height: 1.55; }
  .bubble.in { background: #FFFFFF; border: 1px solid #E5E7EB; color: #111827; border-bottom-left-radius: 3px; }
  .bubble.out { background: #2563EB; color: #FFFFFF; border-bottom-right-radius: 3px; }
  .empty-convo { color: #D1D5DB; font-size: 13px; text-align: center; padding: 36px 20px; }

  /* ── COLLAPSIBLE SECTIONS (shared) ── */
  .section-toggle-row {
    padding: 8px 18px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    cursor: pointer;
    user-select: none;
    border-top: 1px solid #F3F4F6;
    background: #FFFFFF;
  }

  .section-toggle-row:hover { background: #FAFAFA; }

  .section-label {
    font-size: 11px;
    font-weight: 600;
    color: #9CA3AF;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .section-badge {
    background: #F3F4F6;
    color: #6B7280;
    font-size: 10px;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 10px;
  }

  .section-chevron { font-size: 9px; color: #C4C9D4; transition: transform 0.2s; display: inline-block; }
  .section-chevron.open { transform: rotate(180deg); }

  /* ── NOTES ── */
  .notes-body { padding: 0 18px 11px; display: none; background: #FFFFFF; }
  .notes-body.open { display: block; }

  .notes-list {
    max-height: 100px;
    overflow-y: auto;
    margin-bottom: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .notes-list:empty::after { content: 'No notes yet'; display: block; font-size: 11px; color: #D1D5DB; text-align: center; padding: 6px 0; font-style: italic; }
  .notes-list::-webkit-scrollbar { width: 3px; }
  .notes-list::-webkit-scrollbar-thumb { background: #E5E7EB; }

  .note-item { background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 7px; padding: 7px 10px; }
  .note-text { font-size: 12px; color: #78350F; line-height: 1.45; }
  .note-time { font-size: 10px; color: #B45309; margin-top: 3px; }

  .notes-compose { display: flex; gap: 7px; align-items: flex-end; }

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
    transition: border-color 0.15s;
    line-height: 1.4;
  }

  .notes-compose textarea::placeholder { color: #C4C9D4; }
  .notes-compose textarea:focus { border-color: #93C5FD; background: #FFFFFF; }

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
    height: 52px;
    font-family: inherit;
  }

  .btn-save-note:hover { background: #EFF6FF; border-color: #BFDBFE; color: #1D4ED8; }
  .btn-save-note:disabled { background: #F3F4F6; color: #C4C9D4; cursor: not-allowed; }

  /* ── TEMPLATES ── */
  .tpl-body { padding: 0 18px 11px; display: none; background: #FFFFFF; }
  .tpl-body.open { display: block; }
  .tpl-list { display: flex; flex-direction: column; gap: 5px; }

  .tpl-btn {
    width: 100%;
    text-align: left;
    background: #F9FAFB;
    border: 1px solid #E5E7EB;
    border-radius: 7px;
    padding: 8px 11px;
    font-size: 11px;
    color: #374151;
    cursor: pointer;
    line-height: 1.45;
    transition: all 0.12s;
    font-family: inherit;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .tpl-btn:hover { background: #EFF6FF; border-color: #BFDBFE; color: #1D4ED8; }
  .tpl-btn:disabled { background: #F3F4F6; color: #9CA3AF; cursor: not-allowed; }
  .tpl-btn.tpl-sent { background: #ECFDF5; border-color: #A7F3D0; color: #065F46; }

  /* ── PANEL FOOTER ── */
  .panel-actions {
    padding: 10px 18px;
    border-top: 1px solid #F3F4F6;
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    flex-shrink: 0;
    background: #FFFFFF;
  }

  .btn { padding: 7px 12px; border-radius: 7px; font-size: 12px; font-weight: 600; cursor: pointer; border: 1px solid; transition: all 0.15s; white-space: nowrap; line-height: 1.4; font-family: inherit; }
  .btn-primary { background: #2563EB; color: #FFFFFF; border-color: #2563EB; }
  .btn-primary:hover { background: #1D4ED8; border-color: #1D4ED8; }
  .btn-ghost { background: #FFFFFF; color: #374151; border-color: #E5E7EB; }
  .btn-ghost:hover { background: #F9FAFB; border-color: #D1D5DB; color: #111827; }
  .btn-danger { background: #FEF2F2; color: #DC2626; border-color: #FECACA; }
  .btn-danger:hover { background: #FEE2E2; }

  /* ── STAGE PICKER ── */
  .stage-picker {
    padding: 9px 18px;
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

  /* ── CALL PICKER ── */
  .call-picker {
    padding: 10px 18px 12px;
    border-top: 1px solid #F3F4F6;
    display: none;
    background: #FAFAFA;
    flex-shrink: 0;
  }

  .call-picker.open { display: block; }

  .call-picker-label { font-size: 11px; font-weight: 600; color: #9CA3AF; text-transform: uppercase; letter-spacing: 0.07em; margin-bottom: 7px; }

  .call-picker input[type="datetime-local"] {
    width: 100%;
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 7px;
    padding: 8px 11px;
    font-size: 13px;
    color: #111827;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s;
  }

  .call-picker input[type="datetime-local"]:focus { border-color: #93C5FD; box-shadow: 0 0 0 3px rgba(37,99,235,0.08); }

  .call-picker-foot { display: flex; justify-content: flex-end; gap: 7px; margin-top: 8px; }

  /* ── REPLY BOX ── */
  .reply-box { padding: 11px 18px 13px; border-top: 1px solid #F3F4F6; display: none; flex-shrink: 0; background: #FFFFFF; }
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
    height: 68px;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s, box-shadow 0.15s;
    line-height: 1.5;
  }

  .reply-box textarea::placeholder { color: #C4C9D4; }
  .reply-box textarea:focus { border-color: #93C5FD; background: #FFFFFF; box-shadow: 0 0 0 3px rgba(37,99,235,0.08); }

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
    font-family: inherit;
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
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
        <circle cx="5" cy="5" r="3.5" stroke="currentColor" stroke-width="1.4"/>
        <path d="M8 8L10.5 10.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
      </svg>
    </span>
    <input class="search-input" type="text" id="search-input" placeholder="Search leads..." oninput="filterCards(this.value)" autocomplete="off">
  </div>

  <select class="date-filter" id="date-filter" onchange="setDateFilter(this.value)">
    <option value="all">All time</option>
    <option value="today">Today</option>
    <option value="7days">Last 7 days</option>
  </select>

  <button class="archive-toggle" id="archive-toggle" onclick="toggleArchive()">Archived</button>

  <div class="header-right">
    <span class="notif-badge" id="notif-badge" onclick="clearBadge()">
      <span class="notif-dot"></span>
      <span id="notif-text"></span>
    </span>

    <div class="stats">
      <div class="stat">
        <div class="stat-n" id="s-total">0</div>
        <div class="stat-l">Total</div>
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
      <div class="stat-divider"></div>
      <div class="stat">
        <div class="stat-n" id="s-rate">0%</div>
        <div class="stat-l">Response</div>
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

  <div class="panel-scroll-area" id="panel-scroll-area">
    <div class="convo" id="p-convo"></div>

    <!-- Notes -->
    <div class="section-toggle-row" onclick="toggleNotesPanel()">
      <span class="section-label">
        Notes
        <span class="section-badge" id="notes-count">0</span>
      </span>
      <span class="section-chevron open" id="notes-chevron">&#9650;</span>
    </div>
    <div class="notes-body open" id="notes-body">
      <div class="notes-list" id="notes-list"></div>
      <div class="notes-compose">
        <textarea id="note-text" placeholder="Add a private note..."></textarea>
        <button class="btn-save-note" id="btn-save-note" onclick="saveNote()">Save</button>
      </div>
    </div>

    <!-- Templates -->
    <div class="section-toggle-row" onclick="toggleTemplates()">
      <span class="section-label">Quick Reply Templates</span>
      <span class="section-chevron" id="tpl-chevron">&#9650;</span>
    </div>
    <div class="tpl-body" id="tpl-body">
      <div class="tpl-list" id="tpl-list"></div>
    </div>
  </div>

  <!-- Footer -->
  <div class="panel-actions">
    <button class="btn btn-primary" id="btn-take" onclick="toggleTakeover()">Take over</button>
    <button class="btn btn-ghost" onclick="togglePicker()">Move stage</button>
    <button class="btn btn-ghost" onclick="toggleCallPicker()">Schedule call</button>
    <button class="btn btn-danger" onclick="archiveLead()">Archive</button>
  </div>
  <div class="stage-picker" id="stage-picker"></div>
  <div class="call-picker" id="call-picker">
    <div class="call-picker-label">Schedule call</div>
    <input type="datetime-local" id="call-time-input">
    <div class="call-picker-foot">
      <button class="btn btn-ghost" onclick="clearCallTime()">Clear</button>
      <button class="btn btn-primary" onclick="saveCallTime()">Save</button>
    </div>
  </div>
  <div class="reply-box" id="reply-box">
    <textarea id="reply-text" placeholder="Type your reply..."></textarea>
    <div class="reply-foot">
      <button class="btn-send" id="btn-send" onclick="sendMsg()">Send</button>
    </div>
  </div>
</div>

<script>
const STAGES = [
  ["NEW",       "New",             "#94A3B8"],
  ["REPLIED",   "Replied",         "#3B82F6"],
  ["CALL_SENT", "Call Asked",      "#F59E0B"],
  ["SCHEDULED", "Scheduled",       "#10B981"],
  ["EVAN",      "Handed to Evan",  "#8B5CF6"],
  ["TAKEOVER",  "Your Turn",       "#F97316"],
  ["DEAD",      "Not Interested",  "#9CA3AF"],
  ["ARCHIVED",  "Archived",        "#6B7280"],
];

const TEMPLATES = [
  "Just following up — are you still free for a quick call today?",
  "Hey, just wanted to check in. We still have a spot open for you if you’re ready to get started!",
  "No worries at all, feel free to reach out whenever you’re ready!",
  "Awesome, are you free for a quick call today so we can get you set up?",
  "That’s a super solid start — the next step is a quick call with one of Nathan’s business partners. He’ll break down how the program works and help you get set up. Are you free shortly?",
  "Totally get it. Just so you know we only have a few spots left — would a quick 10-minute call help you decide?",
  "Family is always the best motivation. We’ve had students build stores doing $10K/month within 2 months. Are you free for a quick call today?",
];

let data = {};
let cur = null;
let notesPanelOpen = true;
let templatesPanelOpen = false;
let showArchived = false;
let dateFilter = "all";

// ── SEARCH ──────────────────────────────────────────────────
function filterCards(query) {
  const q = query.toLowerCase().trim();
  document.querySelectorAll(".col").forEach(col => {
    let visible = 0;
    col.querySelectorAll(".card").forEach(card => {
      const name  = (card.querySelector(".card-name")?.textContent  || "").toLowerCase();
      const phone = (card.querySelector(".card-phone")?.textContent || "").toLowerCase();
      const match = !q || name.includes(q) || phone.includes(q);
      card.style.display = match ? "" : "none";
      if (match) visible++;
    });
    const countEl = col.querySelector(".col-count");
    if (countEl) countEl.textContent = visible;
  });
}

// ── DATE FILTER ──────────────────────────────────────────────
function setDateFilter(val) {
  dateFilter = val;
  renderBoard();
  const q = document.getElementById("search-input").value;
  if (q) filterCards(q);
}

function matchesDateFilter(p) {
  if (dateFilter === "all") return true;
  if (!p.updated_at) return false;
  const t = parseDBTime(p.updated_at);
  if (dateFilter === "today") {
    const d = new Date(); d.setHours(0,0,0,0);
    return t >= d.getTime();
  }
  if (dateFilter === "7days") return t >= Date.now() - 7 * 86400000;
  return true;
}

// ── ARCHIVE TOGGLE ───────────────────────────────────────────
function toggleArchive() {
  showArchived = !showArchived;
  const btn = document.getElementById("archive-toggle");
  btn.classList.toggle("active", showArchived);
  btn.textContent = showArchived ? "Hide archived" : "Archived";
  renderBoard();
}

// ── UNREAD ───────────────────────────────────────────────────
function isUnread(p) {
  const msgs = p.conversation || [];
  const lastGavinIdx = msgs.map(m => m.role).lastIndexOf("gavin");
  if (lastGavinIdx === -1) return msgs.some(m => m.role === "lead");
  return msgs.slice(lastGavinIdx + 1).some(m => m.role === "lead");
}

// ── NOTIFICATIONS ────────────────────────────────────────────
function parseDBTime(s) {
  if (!s) return 0;
  try { return new Date(s.replace(" ", "T") + ":00").getTime(); } catch(e) { return 0; }
}

function getLastVisit() { return parseInt(localStorage.getItem("pipelineLastVisit") || "0", 10); }
function setLastVisit()  { localStorage.setItem("pipelineLastVisit", Date.now().toString()); }

function countNewReplies() {
  const lastVisit = getLastVisit();
  if (!lastVisit) return 0;
  const active = new Set(["REPLIED","CALL_SENT","SCHEDULED","EVAN","TAKEOVER"]);
  let n = 0;
  for (const p of Object.values(data)) {
    if (active.has(p.stage) && parseDBTime(p.updated_at) > lastVisit) n++;
  }
  return n;
}

function updateBadge(n) {
  const badge = document.getElementById("notif-badge");
  const text  = document.getElementById("notif-text");
  if (n > 0) {
    text.textContent = n === 1 ? "1 new reply" : n + " new replies";
    badge.classList.add("visible");
    document.title = "(" + n + ") Pipeline";
  } else {
    badge.classList.remove("visible");
    document.title = "Pipeline";
  }
}

function clearBadge() { setLastVisit(); updateBadge(0); }

function manualLoad() { setLastVisit(); updateBadge(0); load(true); }

// ── CALL TIME ────────────────────────────────────────────────
function formatCallTime(ct) {
  if (!ct) return null;
  try {
    const d = new Date(ct);
    if (isNaN(d.getTime())) return null;
    const today = new Date(); today.setHours(0,0,0,0);
    const tomorrow = new Date(today.getTime() + 86400000);
    const time = d.toLocaleTimeString("en-US", {hour:"numeric", minute:"2-digit"});
    if (d >= today && d < tomorrow) return "Today " + time;
    return d.toLocaleDateString("en-US", {month:"short", day:"numeric"}) + " " + time;
  } catch(e) { return null; }
}

// ── CORE ─────────────────────────────────────────────────────
async function load(isManual) {
  const res  = await fetch("/api/prospects");
  const json = await res.json();
  data = json.prospects || {};
  const total     = json.total     || 0;
  const responded = json.responded || 0;
  document.getElementById("s-total").textContent   = total;
  document.getElementById("s-replied").textContent = json.replied  || 0;
  document.getElementById("s-yours").textContent   = json.takeover || 0;
  document.getElementById("s-rate").textContent    = total > 0 ? Math.round(responded / total * 100) + "%" : "0%";
  renderBoard();
  if (cur && data[cur]) renderPanel(cur);
  if (!isManual) updateBadge(countNewReplies());
  const q = document.getElementById("search-input").value;
  if (q) filterCards(q);
}

function renderBoard() {
  const board = document.getElementById("board");
  board.innerHTML = "";
  STAGES.forEach(([code, label, color]) => {
    if (code === "ARCHIVED" && !showArchived) return;
    const leads = Object.entries(data)
      .filter(([,p]) => p.stage === code)
      .filter(([,p]) => code === "ARCHIVED" || matchesDateFilter(p));
    const col = document.createElement("div");
    col.className = "col" + (code === "ARCHIVED" ? " col-archived" : "");
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
        ${leads.length === 0 ? '<div class="empty-col">No leads</div>' : ""}
      </div>
    `;
    board.appendChild(col);
    const body = col.querySelector(".col-body");
    leads.forEach(([phone, p]) => {
      const unread = isUnread(p);
      const ct     = formatCallTime(p.call_time);
      const last   = p.last_message ? p.last_message.substring(0,52)+(p.last_message.length>52?"…":"") : "";
      const card   = document.createElement("div");
      card.className   = "card" + (unread ? " unread" : "");
      card.draggable   = true;
      card.dataset.phone = phone;
      card.onclick     = () => openPanel(phone);
      card.ondragstart = (e) => { e.dataTransfer.setData("phone", phone); card.classList.add("dragging"); };
      card.ondragend   = () => card.classList.remove("dragging");
      card.innerHTML = `
        <div class="card-name${unread?" unread":""}">${unread?"<span class=\\"unread-dot\\"></span>":""}${p.name || "Unknown"}</div>
        <div class="card-phone">${phone}</div>
        ${last ? `<div class="card-last">${last}</div>` : ""}
        ${ct   ? `<div class="card-call">📅 ${ct}</div>` : ""}
        ${p.updated_at ? `<div class="card-time">${p.updated_at}</div>` : ""}
      `;
      body.appendChild(card);
    });
  });
}

function onDragOver(e) { e.preventDefault(); e.currentTarget.classList.add("drag-over"); }
function onDragLeave(e) { e.currentTarget.classList.remove("drag-over"); }
async function onDrop(e, stage) {
  e.preventDefault();
  e.currentTarget.classList.remove("drag-over");
  const phone = e.dataTransfer.getData("phone");
  if (!phone || !data[phone]) return;
  await fetch("/api/set_stage", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({phone, stage})});
  await load(true);
}

// ── PANEL ────────────────────────────────────────────────────
function openPanel(phone) {
  cur = phone;
  renderPanel(phone);
  document.getElementById("panel-backdrop").classList.add("open");
  document.getElementById("side-panel").classList.add("open");
}

function renderPanel(phone) {
  const p = data[phone];
  if (!p) return;
  document.getElementById("p-name").textContent  = p.name || "Unknown";
  document.getElementById("p-phone").textContent = phone;

  const stageInfo = STAGES.find(([c]) => c === p.stage) || STAGES[0];
  const hex = stageInfo[2];
  const ct  = formatCallTime(p.call_time);
  document.getElementById("p-meta").innerHTML = `
    <span class="stage-badge" style="background:${hex}18;color:${hex};border:1px solid ${hex}38">
      <span class="badge-dot" style="background:${hex}"></span>${stageInfo[1]}
    </span>
    ${p.updated_at ? `<span class="meta-time">${p.updated_at}</span>` : ""}
    ${ct ? `<span class="call-meta-badge">📅 ${ct}</span>` : ""}
  `;

  const convo = document.getElementById("p-convo");
  convo.innerHTML = "";
  const msgs = p.conversation || [];
  if (!msgs.length) { convo.innerHTML = '<div class="empty-convo">No messages yet</div>'; }
  msgs.forEach(msg => {
    const isIn = msg.role === "lead";
    const wrap = document.createElement("div");
    wrap.className = "bubble-wrap" + (isIn ? "" : " out");
    wrap.innerHTML = `<div class="bubble-label">${isIn ? (p.name||"Lead") : "You"}${msg.time?" \xb7 "+msg.time:""}</div><div class="bubble ${isIn?"in":"out"}">${msg.content}</div>`;
    convo.appendChild(wrap);
  });
  const sa = document.getElementById("panel-scroll-area");
  setTimeout(() => { sa.scrollTop = convo.offsetTop + convo.scrollHeight; }, 60);

  renderNotes(p.notes || []);
  renderTemplates();

  const callInput = document.getElementById("call-time-input");
  callInput.value = p.call_time || "";

  const btnTake  = document.getElementById("btn-take");
  const replyBox = document.getElementById("reply-box");
  if (p.takeover) {
    btnTake.textContent = "Resume auto";
    btnTake.className   = "btn btn-ghost";
    replyBox.classList.add("open");
  } else {
    btnTake.textContent = "Take over";
    btnTake.className   = "btn btn-primary";
    replyBox.classList.remove("open");
  }

  const picker = document.getElementById("stage-picker");
  picker.classList.remove("open");
  picker.innerHTML = STAGES.filter(([c]) => c !== "ARCHIVED").map(([code, label, color]) =>
    `<span class="stage-opt" onclick="setStage('${code}')"><span class="stage-opt-dot" style="background:${color}"></span>${label}</span>`
  ).join("");

  document.getElementById("call-picker").classList.remove("open");
}

function closePanel() {
  document.getElementById("panel-backdrop").classList.remove("open");
  document.getElementById("side-panel").classList.remove("open");
  document.getElementById("stage-picker").classList.remove("open");
  document.getElementById("call-picker").classList.remove("open");
  cur = null;
}

async function toggleTakeover() {
  if (!cur) return;
  const p = data[cur];
  await fetch("/api/takeover", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({phone:cur, takeover:!p.takeover})});
  await load(true);
}

async function sendMsg() {
  if (!cur) return;
  const text = document.getElementById("reply-text").value.trim();
  if (!text) return;
  const btn = document.getElementById("btn-send");
  btn.disabled = true; btn.textContent = "Sending…";
  const res  = await fetch("/api/send", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({phone:cur, message:text})});
  const json = await res.json();
  if (json.ok) { document.getElementById("reply-text").value = ""; await load(true); }
  else alert("Failed: " + (json.error || "unknown error"));
  btn.disabled = false; btn.textContent = "Send";
}

function togglePicker() { document.getElementById("stage-picker").classList.toggle("open"); }

async function setStage(stage) {
  if (!cur) return;
  await fetch("/api/set_stage", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({phone:cur, stage})});
  document.getElementById("stage-picker").classList.remove("open");
  await load(true);
}

// ── NOTES ────────────────────────────────────────────────────
function renderNotes(notes) {
  const list  = document.getElementById("notes-list");
  const count = document.getElementById("notes-count");
  list.innerHTML = "";
  count.textContent = notes.length;
  notes.forEach(n => {
    const item = document.createElement("div");
    item.className = "note-item";
    item.innerHTML = `<div class="note-text">${n.text.replace(/</g,"&lt;").replace(/>/g,"&gt;")}</div><div class="note-time">${n.time}</div>`;
    list.appendChild(item);
  });
  setTimeout(() => list.scrollTop = list.scrollHeight, 30);
}

async function saveNote() {
  if (!cur) return;
  const text = document.getElementById("note-text").value.trim();
  if (!text) return;
  const btn = document.getElementById("btn-save-note");
  btn.disabled = true; btn.textContent = "Saving…";
  const res  = await fetch("/api/save_note", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({phone:cur, note:text})});
  const json = await res.json();
  if (json.ok) { document.getElementById("note-text").value = ""; await load(true); }
  else alert("Failed: " + (json.error||"unknown error"));
  btn.disabled = false; btn.textContent = "Save";
}

function toggleNotesPanel() {
  notesPanelOpen = !notesPanelOpen;
  document.getElementById("notes-body").classList.toggle("open", notesPanelOpen);
  document.getElementById("notes-chevron").classList.toggle("open", notesPanelOpen);
}

// ── TEMPLATES ────────────────────────────────────────────────
function renderTemplates() {
  const list = document.getElementById("tpl-list");
  list.innerHTML = "";
  TEMPLATES.forEach(text => {
    const btn = document.createElement("button");
    btn.className = "tpl-btn";
    btn.textContent = text;
    btn.title = text;
    btn.onclick = () => sendTemplate(text, btn);
    list.appendChild(btn);
  });
}

async function sendTemplate(text, btn) {
  if (!cur) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Sending…";
  const res  = await fetch("/api/send", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({phone:cur, message:text})});
  const json = await res.json();
  if (json.ok) {
    btn.classList.add("tpl-sent");
    btn.textContent = "Sent ✓";
    setTimeout(() => { btn.classList.remove("tpl-sent"); btn.textContent = orig; btn.disabled = false; }, 1800);
    await load(true);
  } else {
    alert("Failed: " + (json.error||"unknown error"));
    btn.textContent = orig;
    btn.disabled = false;
  }
}

function toggleTemplates() {
  templatesPanelOpen = !templatesPanelOpen;
  document.getElementById("tpl-body").classList.toggle("open", templatesPanelOpen);
  document.getElementById("tpl-chevron").classList.toggle("open", templatesPanelOpen);
}

// ── CALL TIME ────────────────────────────────────────────────
function toggleCallPicker() { document.getElementById("call-picker").classList.toggle("open"); }

async function saveCallTime() {
  if (!cur) return;
  const val = document.getElementById("call-time-input").value;
  const res  = await fetch("/api/save_call_time", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({phone:cur, call_time:val})});
  const json = await res.json();
  if (json.ok) { document.getElementById("call-picker").classList.remove("open"); await load(true); }
  else alert("Failed: " + (json.error||"unknown error"));
}

async function clearCallTime() {
  if (!cur) return;
  const res  = await fetch("/api/save_call_time", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({phone:cur, call_time:""})});
  const json = await res.json();
  if (json.ok) { document.getElementById("call-picker").classList.remove("open"); await load(true); }
}

// ── ARCHIVE ──────────────────────────────────────────────────
async function archiveLead() {
  if (!cur) return;
  await fetch("/api/set_stage", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({phone:cur, stage:"ARCHIVED"})});
  closePanel();
  await load(true);
}

// ── INIT ─────────────────────────────────────────────────────
document.addEventListener("keydown", e => { if (e.key === "Escape") closePanel(); });

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
    pipeline = {k: v for k, v in all_p.items() if v.get("stage") != "ARCHIVED"}
    active = {"REPLIED", "CALL_SENT", "SCHEDULED", "EVAN", "TAKEOVER"}
    return jsonify({
        "total": len(pipeline),
        "replied": sum(1 for p in pipeline.values() if p.get("stage") in ["REPLIED", "CALL_SENT"]),
        "takeover": sum(1 for p in pipeline.values() if p.get("stage") == "TAKEOVER"),
        "responded": sum(1 for p in pipeline.values() if p.get("stage") in active),
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

@app.route("/api/save_call_time", methods=["POST"])
def api_save_call_time():
    data = request.json
    phone = data.get("phone")
    call_time = data.get("call_time", "").strip()
    if not phone:
        return jsonify({"ok": False, "error": "Missing phone"}), 400
    prospect = get_prospect(phone)
    if not prospect:
        return jsonify({"ok": False, "error": "Prospect not found"}), 404
    prospect["call_time"] = call_time
    save_prospect(phone, prospect)
    return jsonify({"ok": True})

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
