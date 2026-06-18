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
                conversation JSONB DEFAULT '[]'
            )
        """)
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
                'conversation': row['conversation'] if row['conversation'] else []
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
                'conversation': row['conversation'] if row['conversation'] else []
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
            INSERT INTO prospects (phone, name, stage, last_message, updated_at, takeover, conversation)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (phone) DO UPDATE SET
                name = EXCLUDED.name,
                stage = EXCLUDED.stage,
                last_message = EXCLUDED.last_message,
                updated_at = EXCLUDED.updated_at,
                takeover = EXCLUDED.takeover,
                conversation = EXCLUDED.conversation
        """, (
            phone,
            data.get('name', 'Unknown'),
            data.get('stage', 'NEW'),
            data.get('last_message', ''),
            data.get('updated_at', ''),
            data.get('takeover', False),
            json.dumps(data.get('conversation', []))
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

    # Positive reply — send call message
    if is_positive(incoming_msg):
        try:
            client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            client.messages.create(
                body=CALL_MESSAGE,
                from_=TWILIO_FROM_NUMBER,
                to=from_number
            )
            prospect["stage"] = "CALL_SENT"
            prospect["conversation"].append({
                "role": "gavin",
                "content": CALL_MESSAGE,
                "time": datetime.now().strftime("%H:%M")
            })
            print(f"Sent call message to {from_number}")
        except Exception as e:
            print(f"Error sending message: {e}")

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

  :root {
    --bg: #0d0d0d;
    --surface: #161616;
    --surface2: #1e1e1e;
    --border: rgba(255,255,255,0.07);
    --border2: rgba(255,255,255,0.12);
    --text: #f0f0f0;
    --text2: #888;
    --text3: #555;
    --green: #4ade80;
    --orange: #fb923c;
    --blue: #60a5fa;
    --red: #f87171;
    --radius: 10px;
  }

  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

  /* HEADER */
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 12px 20px; display: flex; align-items: center; gap: 20px; flex-shrink: 0; }
  .logo { font-size: 15px; font-weight: 700; color: var(--text); letter-spacing: -0.3px; }
  .logo span { color: var(--green); }
  .stats { display: flex; gap: 16px; margin-left: auto; }
  .stat { display: flex; flex-direction: column; align-items: center; }
  .stat-n { font-size: 20px; font-weight: 700; line-height: 1; }
  .stat-l { font-size: 10px; color: var(--text2); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 2px; }
  .stat.s-blue .stat-n { color: var(--blue); }
  .stat.s-green .stat-n { color: var(--green); }
  .stat.s-orange .stat-n { color: var(--orange); }
  .refresh { background: var(--surface2); border: 1px solid var(--border2); color: var(--text2); padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; transition: all 0.15s; }
  .refresh:hover { color: var(--text); border-color: rgba(255,255,255,0.25); }

  /* BOARD */
  .board { display: flex; flex: 1; overflow-x: auto; overflow-y: hidden; gap: 1px; background: var(--border); }
  .board::-webkit-scrollbar { height: 6px; }
  .board::-webkit-scrollbar-thumb { background: var(--surface2); border-radius: 3px; }

  /* COLUMN */
  .col { background: var(--bg); display: flex; flex-direction: column; min-width: 220px; flex: 1; }
  .col-head { padding: 14px 14px 10px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .col-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; }
  .col-count { font-size: 11px; font-weight: 600; background: var(--surface2); padding: 2px 7px; border-radius: 20px; color: var(--text2); }
  .col-body { flex: 1; overflow-y: auto; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
  .col-body::-webkit-scrollbar { width: 3px; }
  .col-body::-webkit-scrollbar-thumb { background: var(--surface2); }
  .col-body.drag-over { background: rgba(255,255,255,0.03); outline: 1.5px dashed var(--border2); outline-offset: -4px; border-radius: 6px; }
  .empty { color: var(--text3); font-size: 12px; text-align: center; padding: 24px 10px; }

  /* CARD */
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 11px 13px; cursor: grab; transition: border-color 0.15s, transform 0.1s; user-select: none; }
  .card:hover { border-color: var(--border2); }
  .card.dragging { opacity: 0.45; transform: scale(0.97); cursor: grabbing; }
  .card-name { font-size: 13px; font-weight: 600; color: var(--text); margin-bottom: 3px; }
  .card-phone { font-size: 11px; color: var(--text3); margin-bottom: 7px; font-family: 'SF Mono', monospace; }
  .card-last { font-size: 11px; color: var(--text2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card-time { font-size: 10px; color: var(--text3); margin-top: 5px; }

  /* MODAL */
  .overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 50; align-items: center; justify-content: center; backdrop-filter: blur(2px); }
  .overlay.open { display: flex; }
  .modal { background: var(--surface); border: 1px solid var(--border2); border-radius: 14px; width: 480px; max-width: 94vw; max-height: 84vh; display: flex; flex-direction: column; }
  .modal-top { padding: 18px 20px 14px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: flex-start; }
  .modal-name { font-size: 18px; font-weight: 700; }
  .modal-phone { font-size: 12px; color: var(--text2); margin-top: 2px; font-family: 'SF Mono', monospace; }
  .modal-close { background: var(--surface2); border: none; color: var(--text2); width: 28px; height: 28px; border-radius: 50%; cursor: pointer; font-size: 16px; display: flex; align-items: center; justify-content: center; }
  .modal-close:hover { color: var(--text); }
  .modal-tags { padding: 10px 20px; border-bottom: 1px solid var(--border); display: flex; gap: 8px; flex-wrap: wrap; }
  .tag { font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 20px; }
  .convo { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 10px; }
  .convo::-webkit-scrollbar { width: 3px; }
  .convo::-webkit-scrollbar-thumb { background: var(--surface2); }
  .bubble-wrap { display: flex; flex-direction: column; }
  .bubble-wrap.out { align-items: flex-end; }
  .bubble-label { font-size: 10px; color: var(--text3); margin-bottom: 3px; }
  .bubble { max-width: 76%; padding: 9px 13px; border-radius: 12px; font-size: 13px; line-height: 1.5; }
  .bubble.in { background: var(--surface2); color: var(--text); border-bottom-left-radius: 3px; }
  .bubble.out { background: #1a3a20; color: #b0f0b0; border-bottom-right-radius: 3px; }
  .empty-convo { color: var(--text3); font-size: 13px; text-align: center; padding: 30px; }
  .modal-actions { padding: 12px 20px; border-top: 1px solid var(--border); display: flex; gap: 8px; flex-wrap: wrap; }
  .btn { padding: 8px 14px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; border: 1px solid; transition: all 0.15s; }
  .btn-take { color: var(--orange); border-color: rgba(251,146,60,0.3); background: rgba(251,146,60,0.08); }
  .btn-take:hover { background: rgba(251,146,60,0.15); }
  .btn-resume { color: var(--green); border-color: rgba(74,222,128,0.3); background: rgba(74,222,128,0.08); }
  .btn-resume:hover { background: rgba(74,222,128,0.15); }
  .btn-stage { color: var(--blue); border-color: rgba(96,165,250,0.3); background: rgba(96,165,250,0.08); font-size: 12px; }
  .btn-stage:hover { background: rgba(96,165,250,0.15); }
  .stage-picker { padding: 10px 20px; border-top: 1px solid var(--border); display: none; flex-wrap: wrap; gap: 6px; }
  .stage-picker.open { display: flex; }
  .stage-opt { font-size: 11px; padding: 5px 11px; border-radius: 20px; cursor: pointer; border: 1px solid var(--border2); background: var(--surface2); color: var(--text2); transition: all 0.12s; }
  .stage-opt:hover { color: var(--text); border-color: rgba(255,255,255,0.25); }
  .reply-box { padding: 12px 20px; border-top: 1px solid var(--border); display: none; }
  .reply-box.open { display: block; }
  .reply-box textarea { width: 100%; background: var(--bg); border: 1px solid var(--border2); border-radius: 8px; color: var(--text); padding: 10px 13px; font-size: 13px; resize: none; height: 72px; font-family: inherit; outline: none; }
  .reply-box textarea:focus { border-color: rgba(255,255,255,0.25); }
  .reply-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
  .reply-hint { font-size: 11px; color: var(--text3); }
  .btn-send { background: var(--green); color: #000; padding: 8px 18px; border-radius: 8px; font-weight: 700; font-size: 13px; border: none; cursor: pointer; }
  .btn-send:hover { background: #22c55e; }
  .btn-send:disabled { background: var(--surface2); color: var(--text3); cursor: not-allowed; }
</style>
</head>
<body>

<header>
  <div class="logo">Gavin's <span>Pipeline</span></div>
  <div class="stats">
    <div class="stat s-blue"><div class="stat-n" id="s-total">0</div><div class="stat-l">Total</div></div>
    <div class="stat s-green"><div class="stat-n" id="s-replied">0</div><div class="stat-l">Replied</div></div>
    <div class="stat s-orange"><div class="stat-n" id="s-yours">0</div><div class="stat-l">Your turn</div></div>
  </div>
  <button class="refresh" onclick="load()">Refresh</button>
</header>

<div class="board" id="board"></div>

<div class="overlay" id="overlay">
  <div class="modal">
    <div class="modal-top">
      <div><div class="modal-name" id="m-name"></div><div class="modal-phone" id="m-phone"></div></div>
      <button class="modal-close" onclick="closeModal()">×</button>
    </div>
    <div class="modal-tags" id="m-tags"></div>
    <div class="convo" id="m-convo"></div>
    <div class="modal-actions">
      <button class="btn" id="btn-take" onclick="toggleTakeover()">Take over</button>
      <button class="btn btn-stage" onclick="togglePicker()">Move stage</button>
    </div>
    <div class="stage-picker" id="stage-picker"></div>
    <div class="reply-box" id="reply-box">
      <textarea id="reply-text" placeholder="Type your message…"></textarea>
      <div class="reply-foot">
        <span class="reply-hint">You're in control</span>
        <button class="btn-send" id="btn-send" onclick="sendMsg()">Send</button>
      </div>
    </div>
  </div>
</div>

<script>
const STAGES = [
  ["NEW",       "New",            "#555"],
  ["REPLIED",   "Replied",        "#60a5fa"],
  ["CALL_SENT", "Call asked",     "#f59e0b"],
  ["SCHEDULED", "Scheduled",      "#2dd4bf"],
  ["TAKEOVER",  "Your turn",      "#fb923c"],
  ["DEAD",      "Not interested", "#444"],
];

let data = {};
let cur = null;

async function load() {
  const res = await fetch('/api/prospects');
  const json = await res.json();
  data = json.prospects || {};
  document.getElementById('s-total').textContent = json.total || 0;
  document.getElementById('s-replied').textContent = json.replied || 0;
  document.getElementById('s-yours').textContent = json.takeover || 0;
  renderBoard();
  if (cur && data[cur]) renderModal(cur);
}

function renderBoard() {
  const board = document.getElementById('board');
  board.innerHTML = '';
  STAGES.forEach(([code, label, color]) => {
    const leads = Object.entries(data).filter(([,p]) => p.stage === code);
    const col = document.createElement('div');
    col.className = 'col';
    col.dataset.stage = code;
    col.innerHTML = `
      <div class="col-head">
        <span class="col-title" style="color:${color}">${label}</span>
        <span class="col-count">${leads.length}</span>
      </div>
      <div class="col-body" id="body-${code}" 
           ondragover="onDragOver(event)" 
           ondragleave="onDragLeave(event)" 
           ondrop="onDrop(event,'${code}')">
        ${leads.length === 0 ? '<div class="empty">No leads</div>' : ''}
      </div>
    `;
    board.appendChild(col);
    const body = col.querySelector('.col-body');
    leads.forEach(([phone, p]) => {
      const card = document.createElement('div');
      card.className = 'card';
      card.draggable = true;
      card.dataset.phone = phone;
      card.onclick = () => openModal(phone);
      card.ondragstart = (e) => { e.dataTransfer.setData('phone', phone); card.classList.add('dragging'); };
      card.ondragend = () => card.classList.remove('dragging');
      const last = p.last_message ? p.last_message.substring(0,48)+(p.last_message.length>48?'…':'') : '';
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

function onDragOver(e) {
  e.preventDefault();
  e.currentTarget.classList.add('drag-over');
}
function onDragLeave(e) {
  e.currentTarget.classList.remove('drag-over');
}
async function onDrop(e, stage) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  const phone = e.dataTransfer.getData('phone');
  if (!phone || !data[phone]) return;
  await fetch('/api/set_stage', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({phone, stage}) });
  await load();
}

function openModal(phone) {
  cur = phone;
  renderModal(phone);
  document.getElementById('overlay').classList.add('open');
}

function renderModal(phone) {
  const p = data[phone];
  if (!p) return;
  document.getElementById('m-name').textContent = p.name || 'Unknown';
  document.getElementById('m-phone').textContent = phone;
  const stageInfo = STAGES.find(([c]) => c === p.stage) || STAGES[0];
  document.getElementById('m-tags').innerHTML = `
    <span class="tag" style="background:${stageInfo[2]}22;color:${stageInfo[2]};border:1px solid ${stageInfo[2]}44">${stageInfo[1]}</span>
    ${p.updated_at ? `<span class="tag" style="background:var(--surface2);color:var(--text2);border:1px solid var(--border)">${p.updated_at}</span>` : ''}
  `;
  const convo = document.getElementById('m-convo');
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
  const btnTake = document.getElementById('btn-take');
  const replyBox = document.getElementById('reply-box');
  if (p.takeover) {
    btnTake.textContent = 'Resume auto';
    btnTake.className = 'btn btn-resume';
    replyBox.classList.add('open');
  } else {
    btnTake.textContent = 'Take over';
    btnTake.className = 'btn btn-take';
    replyBox.classList.remove('open');
  }
  const picker = document.getElementById('stage-picker');
  picker.classList.remove('open');
  picker.innerHTML = STAGES.map(([code,label]) => `<span class="stage-opt" onclick="setStage('${code}')">${label}</span>`).join('');
}

function closeModal() {
  document.getElementById('overlay').classList.remove('open');
  document.getElementById('stage-picker').classList.remove('open');
  cur = null;
}

async function toggleTakeover() {
  if (!cur) return;
  const p = data[cur];
  await fetch('/api/takeover', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({phone:cur, takeover:!p.takeover}) });
  await load();
}

async function sendMsg() {
  if (!cur) return;
  const text = document.getElementById('reply-text').value.trim();
  if (!text) return;
  const btn = document.getElementById('btn-send');
  btn.disabled = true; btn.textContent = 'Sending…';
  const res = await fetch('/api/send', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({phone:cur, message:text}) });
  const json = await res.json();
  if (json.ok) { document.getElementById('reply-text').value = ''; await load(); }
  else alert('Failed: ' + (json.error || 'unknown error'));
  btn.disabled = false; btn.textContent = 'Send';
}

function togglePicker() { document.getElementById('stage-picker').classList.toggle('open'); }

async function setStage(stage) {
  if (!cur) return;
  await fetch('/api/set_stage', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({phone:cur, stage}) });
  document.getElementById('stage-picker').classList.remove('open');
  await load();
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
document.getElementById('overlay').addEventListener('click', e => { if (e.target === document.getElementById('overlay')) closeModal(); });

load();
setInterval(load, 30000);
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

@app.route("/")
def health():
    return f"SMS System running | <a href='/dashboard'>Dashboard</a>", 200

# Initialize DB on startup
init_db()

if __name__ == "__main__":
    print("Starting server on http://localhost:5000")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
