#!/usr/bin/env python3
"""
SMS System - Simple 2-message flow
Message 1: Blast opener (sent via blast.py)
Message 2: If they respond positively, ask if they're free for a call
Then Gavin takes over
"""

from flask import Flask, request, jsonify, render_template_string
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import json
import os
from datetime import datetime

app = Flask(__name__)

# ============================================================
# CONFIGURATION
# ============================================================
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")
DATA_FILE          = "prospects.json"

# ============================================================
# STAGES
# ============================================================
STAGES = [
    ("NEW",         "New"),
    ("REPLIED",     "Replied"),
    ("INTERESTED",  "Interested ✅"),
    ("CALL_SENT",   "Call Asked 📞"),
    ("SCHEDULED",   "Call Scheduled 📅"),
    ("TAKEOVER",    "Gavin Taking Over 👤"),
    ("DEAD",        "Not Interested ❌"),
]

STAGE_COLORS = {
    "NEW":        "#555",
    "REPLIED":    "#1a6fbd",
    "INTERESTED": "#1a8a3a",
    "CALL_SENT":  "#b57a00",
    "SCHEDULED":  "#0a7a8a",
    "TAKEOVER":   "#fb923c",
    "DEAD":       "#444",
}

# ============================================================
# POSITIVE / NEGATIVE DETECTION
# ============================================================
POSITIVE_KEYWORDS = [
    "yes", "yeah", "yep", "yup", "sure", "absolutely", "definitely",
    "interested", "still", "of course", "sounds good", "ok", "okay",
    "i am", "i do", "for sure", "lets do it", "let's do it", "correct",
    "affirmative", "indeed", "totally", "100", "drop", "dropshipping",
    "want to", "id like", "i'd like", "please", "sign me up"
]

NEGATIVE_KEYWORDS = [
    "no", "nope", "not interested", "stop", "unsubscribe", "remove",
    "dont contact", "don't contact", "leave me alone", "not anymore",
    "changed my mind", "never mind", "nevermind", "cancel"
]

def is_positive(message):
    msg = message.lower().strip()
    for word in NEGATIVE_KEYWORDS:
        if word in msg:
            return False
    for word in POSITIVE_KEYWORDS:
        if word in msg:
            return True
    # Default to positive if short reply (like "yes", "k", "ok")
    if len(msg) < 20:
        return True
    return True  # Default positive unless clearly negative

def is_negative(message):
    msg = message.lower().strip()
    for word in NEGATIVE_KEYWORDS:
        if word in msg:
            return True
    return False

# ============================================================
# LOAD / SAVE
# ============================================================
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

prospects = load_data()

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

    if from_number not in prospects:
        prospects[from_number] = {
            "name": "Unknown",
            "stage": "NEW",
            "last_message": "",
            "updated_at": "",
            "takeover": False,
            "conversation": []
        }

    prospects[from_number]["last_message"] = incoming_msg
    prospects[from_number]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    prospects[from_number]["conversation"].append({
        "role": "lead",
        "content": incoming_msg,
        "time": datetime.now().strftime("%H:%M")
    })

    # If Gavin has taken over, just log and don't auto-reply
    if prospects[from_number].get("takeover"):
        save_data(prospects)
        print(f"Takeover active for {from_number} — no auto reply sent")
        return ('', 204)

    # Already sent call message — Gavin takes over
    if prospects[from_number]["stage"] in ["CALL_SENT", "SCHEDULED", "TAKEOVER"]:
        prospects[from_number]["takeover"] = True
        prospects[from_number]["stage"] = "TAKEOVER"
        save_data(prospects)
        print(f"Moving {from_number} to Gavin takeover")
        return ('', 204)

    # Dead lead — don't reply
    if prospects[from_number]["stage"] == "DEAD":
        save_data(prospects)
        return ('', 204)

    # First reply — check if positive or negative
    if is_negative(incoming_msg):
        prospects[from_number]["stage"] = "DEAD"
        save_data(prospects)
        print(f"{from_number} marked as not interested")
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
            prospects[from_number]["stage"] = "CALL_SENT"
            prospects[from_number]["conversation"].append({
                "role": "gavin",
                "content": CALL_MESSAGE,
                "time": datetime.now().strftime("%H:%M")
            })
            print(f"Sent call message to {from_number}")
        except Exception as e:
            print(f"Error sending message: {e}")

    save_data(prospects)
    return ('', 204)

# ============================================================
# DASHBOARD
# ============================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gavin's Pipeline</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f0f0f; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; height: 100vh; overflow: hidden; }
  .header { background: #1a1a1a; border-bottom: 1px solid #2a2a2a; padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 18px; font-weight: 700; color: #fff; }
  .header h1 span { color: #4ade80; }
  .stats { display: flex; gap: 20px; }
  .stat { text-align: center; }
  .stat-num { font-size: 22px; font-weight: 700; }
  .stat-label { font-size: 11px; color: #888; text-transform: uppercase; }
  .stat.green .stat-num { color: #4ade80; }
  .stat.orange .stat-num { color: #fb923c; }
  .stat.blue .stat-num { color: #60a5fa; }
  .refresh-btn { background: #2a2a2a; border: 1px solid #3a3a3a; color: #ccc; padding: 7px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .pipeline { display: flex; gap: 0; height: calc(100vh - 57px); overflow-x: auto; }
  .column { min-width: 200px; max-width: 220px; border-right: 1px solid #1e1e1e; display: flex; flex-direction: column; flex-shrink: 0; }
  .col-header { padding: 10px 12px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #1e1e1e; display: flex; justify-content: space-between; align-items: center; }
  .col-count { background: #2a2a2a; border-radius: 10px; padding: 1px 7px; font-size: 11px; }
  .col-body { flex: 1; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 8px; }
  .card { background: #1c1c1c; border: 1px solid #2a2a2a; border-radius: 8px; padding: 10px 12px; cursor: pointer; transition: all 0.15s; }
  .card:hover { background: #232323; border-color: #444; }
  .card-name { font-size: 13px; font-weight: 600; color: #fff; margin-bottom: 3px; }
  .card-phone { font-size: 11px; color: #666; margin-bottom: 6px; }
  .card-last { font-size: 11px; color: #555; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .empty-col { color: #333; font-size: 12px; text-align: center; padding: 20px 10px; }
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 100; align-items: center; justify-content: center; }
  .modal-overlay.open { display: flex; }
  .modal { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px; width: 520px; max-width: 95vw; max-height: 85vh; display: flex; flex-direction: column; }
  .modal-header { padding: 16px 20px; border-bottom: 1px solid #2a2a2a; display: flex; justify-content: space-between; align-items: flex-start; }
  .modal-name { font-size: 17px; font-weight: 700; color: #fff; }
  .modal-phone { font-size: 12px; color: #666; margin-top: 2px; }
  .modal-close { background: none; border: none; color: #666; font-size: 20px; cursor: pointer; }
  .modal-close:hover { color: #fff; }
  .modal-meta { padding: 10px 20px; border-bottom: 1px solid #1e1e1e; display: flex; gap: 10px; flex-wrap: wrap; }
  .meta-pill { font-size: 11px; padding: 3px 10px; border-radius: 20px; font-weight: 600; }
  .convo { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 8px; }
  .msg { max-width: 78%; padding: 9px 13px; border-radius: 12px; font-size: 13px; line-height: 1.45; }
  .msg.lead { background: #2a2a2a; color: #e0e0e0; align-self: flex-start; border-bottom-left-radius: 3px; }
  .msg.gavin { background: #1a3a1a; color: #d0ffd0; align-self: flex-end; border-bottom-right-radius: 3px; }
  .msg-wrap { display: flex; flex-direction: column; }
  .msg-wrap.right { align-items: flex-end; }
  .msg-label { font-size: 10px; color: #555; margin-bottom: 2px; }
  .modal-actions { padding: 12px 20px; border-top: 1px solid #1e1e1e; display: flex; gap: 8px; flex-wrap: wrap; }
  .btn { padding: 8px 14px; border-radius: 7px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; }
  .btn-takeover { background: #fb923c22; color: #fb923c; border: 1px solid #fb923c55; }
  .btn-takeover:hover { background: #fb923c33; }
  .btn-resume { background: #4ade8022; color: #4ade80; border: 1px solid #4ade8055; }
  .btn-stage { background: #60a5fa22; color: #60a5fa; border: 1px solid #60a5fa55; font-size: 12px; }
  .reply-area { padding: 12px 20px; border-top: 1px solid #1e1e1e; display: none; }
  .reply-area.visible { display: block; }
  .reply-area textarea { width: 100%; background: #111; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; padding: 10px 12px; font-size: 13px; resize: none; height: 70px; font-family: inherit; }
  .reply-area textarea:focus { outline: none; border-color: #555; }
  .reply-row { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
  .reply-hint { font-size: 11px; color: #555; }
  .btn-send { background: #4ade80; color: #000; padding: 8px 18px; border-radius: 7px; font-weight: 700; font-size: 13px; border: none; cursor: pointer; }
  .btn-send:hover { background: #22c55e; }
  .btn-send:disabled { background: #333; color: #666; cursor: not-allowed; }
  .stage-select { display: none; padding: 10px 20px; border-top: 1px solid #1e1e1e; flex-wrap: wrap; gap: 6px; }
  .stage-select.visible { display: flex; }
  .stage-opt { font-size: 11px; padding: 4px 10px; border-radius: 20px; cursor: pointer; border: 1px solid #333; background: #1e1e1e; color: #aaa; }
  .stage-opt:hover { background: #2a2a2a; color: #fff; }
</style>
</head>
<body>
<div class="header">
  <h1>Gavin's <span>Pipeline</span></h1>
  <div class="stats">
    <div class="stat blue"><div class="stat-num" id="stat-total">0</div><div class="stat-label">Total</div></div>
    <div class="stat green"><div class="stat-num" id="stat-interested">0</div><div class="stat-label">Interested</div></div>
    <div class="stat orange"><div class="stat-num" id="stat-takeover">0</div><div class="stat-label">Your Turn</div></div>
  </div>
  <button class="refresh-btn" onclick="loadPipeline()">Refresh</button>
</div>
<div class="pipeline" id="pipeline"></div>
<div class="modal-overlay" id="modal">
  <div class="modal">
    <div class="modal-header">
      <div><div class="modal-name" id="modal-name"></div><div class="modal-phone" id="modal-phone"></div></div>
      <button class="modal-close" onclick="closeModal()">x</button>
    </div>
    <div class="modal-meta" id="modal-meta"></div>
    <div class="convo" id="modal-convo"></div>
    <div class="modal-actions">
      <button class="btn btn-takeover" id="btn-takeover" onclick="toggleTakeover()">Take Over</button>
      <button class="btn btn-stage" onclick="toggleStageSelect()">Change Stage</button>
    </div>
    <div class="stage-select" id="stage-select"></div>
    <div class="reply-area" id="reply-area">
      <textarea id="reply-text" placeholder="Type your message..."></textarea>
      <div class="reply-row">
        <span class="reply-hint">You are in control</span>
        <button class="btn-send" id="btn-send" onclick="sendManual()">Send</button>
      </div>
    </div>
  </div>
</div>
<script>
const STAGES = STAGES_JSON;
const STAGE_COLORS = COLORS_JSON;
let allProspects = {};
let currentPhone = null;

async function loadPipeline() {
  const res = await fetch('/api/prospects');
  const data = await res.json();
  allProspects = data.prospects;
  document.getElementById('stat-total').textContent = data.total;
  document.getElementById('stat-interested').textContent = data.interested;
  document.getElementById('stat-takeover').textContent = data.takeover;
  const pipeline = document.getElementById('pipeline');
  pipeline.innerHTML = '';
  STAGES.forEach(([code, label]) => {
    const leads = Object.entries(allProspects).filter(([,p]) => p.stage === code);
    const col = document.createElement('div');
    col.className = 'column';
    col.innerHTML = `<div class="col-header" style="color:${STAGE_COLORS[code]||'#aaa'}">${label}<span class="col-count">${leads.length}</span></div><div class="col-body" id="col-${code}"></div>`;
    pipeline.appendChild(col);
    const body = col.querySelector('.col-body');
    if (!leads.length) { body.innerHTML = '<div class="empty-col">-</div>'; return; }
    leads.forEach(([phone, p]) => {
      const card = document.createElement('div');
      card.className = 'card';
      card.onclick = () => openModal(phone);
      const last = p.last_message ? p.last_message.substring(0,45)+(p.last_message.length>45?'...':'') : '';
      card.innerHTML = `<div class="card-name">${p.name||'Unknown'}</div><div class="card-phone">${phone}</div>${last?`<div class="card-last">${last}</div>`:''}`;
      body.appendChild(card);
    });
  });
}

function openModal(phone) {
  currentPhone = phone;
  const p = allProspects[phone];
  document.getElementById('modal-name').textContent = p.name||'Unknown';
  document.getElementById('modal-phone').textContent = phone;
  const stageLabel = STAGES.find(([c])=>c===p.stage)?.[1]||p.stage;
  const stageColor = STAGE_COLORS[p.stage]||'#aaa';
  document.getElementById('modal-meta').innerHTML = `<span class="meta-pill" style="background:${stageColor}22;color:${stageColor};border:1px solid ${stageColor}44">${stageLabel}</span>${p.updated_at?`<span class="meta-pill" style="background:#1e1e1e;color:#555;border:1px solid #2a2a2a">${p.updated_at}</span>`:''}`;
  const convo = document.getElementById('modal-convo');
  convo.innerHTML = '';
  (p.conversation||[]).forEach(msg => {
    const isLead = msg.role==='lead';
    const wrap = document.createElement('div');
    wrap.className = 'msg-wrap'+(isLead?'':' right');
    wrap.innerHTML = `<div class="msg-label">${isLead?(p.name||'Lead'):'You'}</div><div class="msg ${isLead?'lead':'gavin'}">${msg.content}</div>`;
    convo.appendChild(wrap);
  });
  setTimeout(()=>convo.scrollTop=convo.scrollHeight,50);
  const btnTakeover = document.getElementById('btn-takeover');
  const replyArea = document.getElementById('reply-area');
  if (p.takeover) {
    btnTakeover.textContent = 'Resume Auto';
    btnTakeover.className = 'btn btn-resume';
    replyArea.classList.add('visible');
  } else {
    btnTakeover.textContent = 'Take Over';
    btnTakeover.className = 'btn btn-takeover';
    replyArea.classList.remove('visible');
  }
  const ss = document.getElementById('stage-select');
  ss.classList.remove('visible');
  ss.innerHTML = STAGES.map(([code,label])=>`<span class="stage-opt" onclick="setStage('${phone}','${code}')">${label}</span>`).join('');
  document.getElementById('modal').classList.add('open');
}

function closeModal() { document.getElementById('modal').classList.remove('open'); currentPhone=null; }

async function toggleTakeover() {
  if (!currentPhone) return;
  const p = allProspects[currentPhone];
  await fetch('/api/takeover',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:currentPhone,takeover:!p.takeover})});
  await loadPipeline(); openModal(currentPhone);
}

async function sendManual() {
  if (!currentPhone) return;
  const text = document.getElementById('reply-text').value.trim();
  if (!text) return;
  const btn = document.getElementById('btn-send');
  btn.disabled=true; btn.textContent='Sending...';
  const res = await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:currentPhone,message:text})});
  const data = await res.json();
  if (data.ok) { document.getElementById('reply-text').value=''; await loadPipeline(); openModal(currentPhone); }
  else alert('Failed: '+(data.error||'unknown'));
  btn.disabled=false; btn.textContent='Send';
}

async function setStage(phone,stage) {
  await fetch('/api/set_stage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone,stage})});
  document.getElementById('stage-select').classList.remove('visible');
  await loadPipeline(); openModal(phone);
}

function toggleStageSelect() { document.getElementById('stage-select').classList.toggle('visible'); }
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();});
document.getElementById('modal').addEventListener('click',e=>{if(e.target===document.getElementById('modal'))closeModal();});
loadPipeline();
setInterval(loadPipeline,30000);
</script>
</body>
</html>
"""

# ============================================================
# API ROUTES
# ============================================================
@app.route("/dashboard")
def dashboard():
    import json as _json
    stages_json = _json.dumps(STAGES)
    colors_json = _json.dumps(STAGE_COLORS)
    html = DASHBOARD_HTML.replace("STAGES_JSON", stages_json).replace("COLORS_JSON", colors_json)
    return html

@app.route("/api/prospects")
def api_prospects():
    return jsonify({
        "total": len(prospects),
        "interested": sum(1 for p in prospects.values() if p.get("stage") in ["INTERESTED","CALL_SENT"]),
        "takeover": sum(1 for p in prospects.values() if p.get("stage") == "TAKEOVER"),
        "prospects": prospects
    })

@app.route("/api/takeover", methods=["POST"])
def api_takeover():
    data = request.json
    phone = data.get("phone")
    takeover = data.get("takeover", True)
    if phone in prospects:
        prospects[phone]["takeover"] = takeover
        save_data(prospects)
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
        if phone in prospects:
            prospects[phone]["conversation"].append({"role": "gavin", "content": message, "time": datetime.now().strftime("%H:%M")})
            prospects[phone]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            save_data(prospects)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/set_stage", methods=["POST"])
def api_set_stage():
    data = request.json
    phone = data.get("phone")
    stage = data.get("stage")
    if phone in prospects:
        prospects[phone]["stage"] = stage
        save_data(prospects)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404

@app.route("/")
def health():
    return f"SMS System running | {len(prospects)} prospects | <a href='/dashboard'>Dashboard</a>", 200

if __name__ == "__main__":
    print("Starting server on http://localhost:5000")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
