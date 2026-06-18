#!/usr/bin/env python3
"""
SMS AI Responder + Dashboard
- Webhook: receives Twilio replies, AI responds using Zoltan's script
- Dashboard: pipeline view, click leads, read convos, takeover, reply
"""

from flask import Flask, request, jsonify, render_template_string
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import anthropic
import random
import time
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
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
DATA_FILE          = "prospects.json"

# ============================================================
# STAGES
# ============================================================
STAGES = [
    ("NEW",              "New"),
    ("INTERESTED",       "Interested"),
    ("QUALIFIED_WHY",    "Got Their Why"),
    ("QUALIFIED_TIME",   "Time ✓"),
    ("QUALIFIED_BUDGET", "Budget ✓"),
    ("CALL_READY",       "Ready for Evan ✅"),
    ("NEEDS_GAVIN",      "Needs Gavin 📞"),
    ("SCHEDULED",        "Scheduled 📅"),
    ("DEAD",             "Dead ❌"),
]
STAGE_COLORS = {
    "NEW":              "#555",
    "INTERESTED":       "#1a6fbd",
    "QUALIFIED_WHY":    "#7b3fb5",
    "QUALIFIED_TIME":   "#b57a00",
    "QUALIFIED_BUDGET": "#b55a00",
    "CALL_READY":       "#1a8a3a",
    "NEEDS_GAVIN":      "#b54800",
    "SCHEDULED":        "#0a7a8a",
    "DEAD":             "#444",
}

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
# SYSTEM PROMPT
# ============================================================
SYSTEM_PROMPT = """
You are Gavin, a setter for Nathan Nazareth's dropshipping coaching program. You are texting leads via SMS who previously opted in showing interest in making money online or starting a dropshipping business.

YOUR GOAL: Qualify leads through a natural SMS conversation, then get them on a call with Evan (the closer). If they're skeptical or have lots of questions, get them on a call with Gavin instead.

YOUR PERSONALITY:
- Casual, friendly, conversational — like texting a friend
- Short messages (1-3 sentences max per text)
- Empathetic — mirror their motivation back to them
- Confident but not pushy
- Never robotic or salesy
- NEVER use emojis in any message. Zero emojis. Plain text only.

THE QUALIFICATION FLOW (follow this order naturally):

STEP 1 — CONFIRM INTEREST
When they reply yes/interested → Say you want to ask a few quick questions before the call to make sure it's a good fit. Mention you only have a few spots open right now.

STEP 2 — ASK THEIR WHY
"What made you interested in starting a dropshipping business?"
→ When they answer, validate their reason genuinely (family, freedom, extra income, leaving 9-5 — all are good motivations)

STEP 3 — CHECK TIME AVAILABILITY
"We've had students build stores that hit $10K/month within 2 months, but it comes down to consistency. Realistically, could you put aside 1-2 hours a day to work on it?"
→ Any yes answer here is good

STEP 4 — CHECK BUDGET
"The next thing I check is whether someone is realistically in a position to get started. Your store will need some money to set up — the cost of opening a Wix account to host your website and a few tools we recommend. How much could you comfortably invest to get yourself going right now?"

BUDGET RULES:
→ They say $150 or more → NEVER mention pricing. Just validate and move to booking the call.
→ They ask "how much is the subscription?" → Say: "There are a few different price points — easiest to go over that on the call so we can get you set up with the right one."
→ They say $50-149 and hesitant → Mention there's a basic option around $50, still push for the call.
→ They say $0 / no money → "No worries — feel free to reach back out when you're in a better position. Good luck!"
→ NEVER volunteer pricing unless they ask AND budget is low.

STEP 5 — BOOK THE CALL
"That's a super solid start. The next step is a quick call with one of Nathan's business partners. He'll break down how the program works, answer your questions, and help you set up your Wix subscription on the call. Are you free for a setup call shortly?"
→ Yes → "Perfect! I'm going to connect you with Evan, he's one of Nathan's business partners. What's the best number to reach you on?"
→ Can't now → "No worries, when works best for you?"

COMMON SITUATIONS:
"Can we do this over text?" → Cover some things here but eventually push for the call.
"Who is this / what is this?" → "Hey! I'm Gavin, I work with Nathan Nazareth's team. You signed up showing interest in making money online through dropshipping — just reaching out to see if you're still looking to get started!"
"Not interested" → "No worries! If you change your mind feel free to reach out. Good luck!"
"Need to think / not right now" → "Totally get it. We only have limited spots though — would a quick 10-minute call help you decide?"
"Is there a cost / card required?" → "No charge for the call — it's free. The only costs are the Wix subscription and tools, which the business partner walks you through on the call."
"How does it work / how do I get products?" → "That's exactly what the call covers — way easier to show you than explain over text. Are you free for a quick call?"
"Busy / call back later" → "Of course! What time works best?"

OFF-TRACK FALLBACK — USE THIS WHENEVER THINGS GET COMPLICATED:
If the conversation goes off track, lead is asking too many questions, going in circles, or you can't move them forward — reset with:
"Honestly it's way easier for me to just explain everything on a quick call — are you free to hop on one now? I can answer everything properly that way."

IMPORTANT RULES:
- NEVER mention pricing unless they ask AND budget is low
- Keep messages SHORT — 1-3 sentences, this is SMS
- Skeptical / lots of questions → push for Gavin call first
- Always warm, human, never robotic
- The goal is always: get them on a call

STAGE TRACKING — append to EVERY reply on a new line (hidden from lead):
STAGE: <code> | BUDGET: <amount or unknown> | WHY: <brief or unknown>

Codes: INTERESTED, QUALIFIED_WHY, QUALIFIED_TIME, QUALIFIED_BUDGET, CALL_READY, NEEDS_GAVIN, SCHEDULED, DEAD
"""

# ============================================================
# PARSE STAGE
# ============================================================
def parse_stage(ai_reply):
    lines = ai_reply.strip().split("\n")
    stage = budget = why = None
    clean_lines = []
    for line in lines:
        if line.startswith("STAGE:"):
            try:
                parts = line.split("|")
                stage  = parts[0].replace("STAGE:", "").strip()
                budget = parts[1].replace("BUDGET:", "").strip()
                why    = parts[2].replace("WHY:", "").strip()
            except:
                pass
        else:
            clean_lines.append(line)
    return "\n".join(clean_lines).strip(), stage, budget, why

# ============================================================
# DASHBOARD HTML
# ============================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gavin's Prospect Pipeline</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f0f0f; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; height: 100vh; overflow: hidden; }

  /* HEADER */
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
  .refresh-btn:hover { background: #333; }

  /* PIPELINE */
  .pipeline { display: flex; gap: 0; height: calc(100vh - 57px); overflow-x: auto; }
  .column { min-width: 200px; max-width: 220px; border-right: 1px solid #1e1e1e; display: flex; flex-direction: column; flex-shrink: 0; }
  .col-header { padding: 10px 12px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #1e1e1e; display: flex; justify-content: space-between; align-items: center; }
  .col-count { background: #2a2a2a; border-radius: 10px; padding: 1px 7px; font-size: 11px; }
  .col-body { flex: 1; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 8px; }
  .col-body::-webkit-scrollbar { width: 4px; }
  .col-body::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }

  /* CARD */
  .card { background: #1c1c1c; border: 1px solid #2a2a2a; border-radius: 8px; padding: 10px 12px; cursor: pointer; transition: all 0.15s; }
  .card:hover { background: #232323; border-color: #444; transform: translateY(-1px); }
  .card.takeover { border-left: 3px solid #fb923c; }
  .card-name { font-size: 13px; font-weight: 600; color: #fff; margin-bottom: 3px; }
  .card-phone { font-size: 11px; color: #666; margin-bottom: 6px; }
  .card-meta { display: flex; gap: 6px; flex-wrap: wrap; }
  .badge { font-size: 10px; padding: 2px 6px; border-radius: 4px; background: #2a2a2a; color: #aaa; }
  .badge.budget { background: #1a3a1a; color: #4ade80; }
  .badge.takeover { background: #3a2010; color: #fb923c; }
  .card-last { font-size: 11px; color: #555; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* MODAL */
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 100; align-items: center; justify-content: center; }
  .modal-overlay.open { display: flex; }
  .modal { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px; width: 520px; max-width: 95vw; max-height: 85vh; display: flex; flex-direction: column; }
  .modal-header { padding: 16px 20px; border-bottom: 1px solid #2a2a2a; display: flex; justify-content: space-between; align-items: flex-start; }
  .modal-name { font-size: 17px; font-weight: 700; color: #fff; }
  .modal-phone { font-size: 12px; color: #666; margin-top: 2px; }
  .modal-close { background: none; border: none; color: #666; font-size: 20px; cursor: pointer; padding: 0 4px; line-height: 1; }
  .modal-close:hover { color: #fff; }
  .modal-meta { padding: 10px 20px; border-bottom: 1px solid #1e1e1e; display: flex; gap: 10px; flex-wrap: wrap; }
  .meta-pill { font-size: 11px; padding: 3px 10px; border-radius: 20px; font-weight: 600; }

  /* CONVO */
  .convo { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 8px; }
  .convo::-webkit-scrollbar { width: 4px; }
  .convo::-webkit-scrollbar-thumb { background: #333; }
  .msg { max-width: 78%; padding: 9px 13px; border-radius: 12px; font-size: 13px; line-height: 1.45; }
  .msg.lead { background: #2a2a2a; color: #e0e0e0; align-self: flex-start; border-bottom-left-radius: 3px; }
  .msg.ai { background: #1a3a5c; color: #d0e8ff; align-self: flex-end; border-bottom-right-radius: 3px; }
  .msg.gavin { background: #1a3a1a; color: #d0ffd0; align-self: flex-end; border-bottom-right-radius: 3px; }
  .msg-label { font-size: 10px; color: #555; margin-bottom: 2px; }
  .msg-wrap { display: flex; flex-direction: column; }
  .msg-wrap.right { align-items: flex-end; }

  /* ACTIONS */
  .modal-actions { padding: 12px 20px; border-top: 1px solid #1e1e1e; display: flex; gap: 8px; }
  .btn { padding: 8px 14px; border-radius: 7px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; transition: all 0.15s; }
  .btn-takeover { background: #fb923c22; color: #fb923c; border: 1px solid #fb923c55; }
  .btn-takeover:hover { background: #fb923c33; }
  .btn-resume { background: #4ade8022; color: #4ade80; border: 1px solid #4ade8055; }
  .btn-resume:hover { background: #4ade8033; }
  .btn-stage { background: #60a5fa22; color: #60a5fa; border: 1px solid #60a5fa55; font-size: 12px; }
  .btn-stage:hover { background: #60a5fa33; }

  /* REPLY */
  .reply-area { padding: 12px 20px; border-top: 1px solid #1e1e1e; display: none; }
  .reply-area.visible { display: block; }
  .reply-area textarea { width: 100%; background: #111; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; padding: 10px 12px; font-size: 13px; resize: none; height: 70px; font-family: inherit; }
  .reply-area textarea:focus { outline: none; border-color: #555; }
  .reply-row { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
  .reply-hint { font-size: 11px; color: #555; }
  .btn-send { background: #4ade80; color: #000; padding: 8px 18px; border-radius: 7px; font-weight: 700; font-size: 13px; border: none; cursor: pointer; }
  .btn-send:hover { background: #22c55e; }
  .btn-send:disabled { background: #333; color: #666; cursor: not-allowed; }

  /* STAGE SELECT */
  .stage-select { display: none; padding: 10px 20px; border-top: 1px solid #1e1e1e; }
  .stage-select.visible { display: flex; gap: 6px; flex-wrap: wrap; }
  .stage-opt { font-size: 11px; padding: 4px 10px; border-radius: 20px; cursor: pointer; border: 1px solid #333; background: #1e1e1e; color: #aaa; }
  .stage-opt:hover { background: #2a2a2a; color: #fff; }

  .empty-col { color: #333; font-size: 12px; text-align: center; padding: 20px 10px; }
</style>
</head>
<body>

<div class="header">
  <h1>Gavin's <span>Prospect Pipeline</span></h1>
  <div class="stats">
    <div class="stat blue"><div class="stat-num" id="stat-total">0</div><div class="stat-label">Total</div></div>
    <div class="stat green"><div class="stat-num" id="stat-evan">0</div><div class="stat-label">Ready for Evan</div></div>
    <div class="stat orange"><div class="stat-num" id="stat-gavin">0</div><div class="stat-label">Needs Gavin</div></div>
  </div>
  <button class="refresh-btn" onclick="loadPipeline()">↻ Refresh</button>
</div>

<div class="pipeline" id="pipeline"></div>

<!-- MODAL -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <div class="modal-header">
      <div>
        <div class="modal-name" id="modal-name"></div>
        <div class="modal-phone" id="modal-phone"></div>
      </div>
      <button class="modal-close" onclick="closeModal()">✕</button>
    </div>
    <div class="modal-meta" id="modal-meta"></div>
    <div class="convo" id="modal-convo"></div>
    <div class="modal-actions">
      <button class="btn btn-takeover" id="btn-takeover" onclick="toggleTakeover()">🙋 Take Over</button>
      <button class="btn btn-stage" onclick="toggleStageSelect()">✏️ Change Stage</button>
    </div>
    <div class="stage-select" id="stage-select"></div>
    <div class="reply-area" id="reply-area">
      <textarea id="reply-text" placeholder="Type your message..."></textarea>
      <div class="reply-row">
        <span class="reply-hint">AI is paused — you're in control</span>
        <button class="btn-send" id="btn-send" onclick="sendManual()">Send ↑</button>
      </div>
    </div>
  </div>
</div>

<script>
const STAGES = {{ stages_json }};
const STAGE_COLORS = {{ colors_json }};
let allProspects = {};
let currentPhone = null;

async function loadPipeline() {
  const res = await fetch('/api/prospects');
  const data = await res.json();
  allProspects = data.prospects;

  document.getElementById('stat-total').textContent = data.total;
  document.getElementById('stat-evan').textContent = data.call_ready;
  document.getElementById('stat-gavin').textContent = data.needs_gavin;

  const pipeline = document.getElementById('pipeline');
  pipeline.innerHTML = '';

  STAGES.forEach(([code, label]) => {
    const leads = Object.entries(allProspects).filter(([,p]) => p.stage === code);
    const col = document.createElement('div');
    col.className = 'column';
    col.innerHTML = `
      <div class="col-header" style="color:${STAGE_COLORS[code] || '#aaa'}">
        ${label}
        <span class="col-count">${leads.length}</span>
      </div>
      <div class="col-body" id="col-${code}"></div>
    `;
    pipeline.appendChild(col);

    const body = col.querySelector('.col-body');
    if (leads.length === 0) {
      body.innerHTML = '<div class="empty-col">—</div>';
    }
    leads.forEach(([phone, p]) => {
      const card = document.createElement('div');
      card.className = 'card' + (p.takeover ? ' takeover' : '');
      card.onclick = () => openModal(phone);
      const budget = p.budget && p.budget !== 'unknown' ? `<span class="badge budget">💰 ${p.budget}</span>` : '';
      const to = p.takeover ? `<span class="badge takeover">You</span>` : '';
      const lastMsg = p.last_message ? p.last_message.substring(0, 45) + (p.last_message.length > 45 ? '…' : '') : '';
      card.innerHTML = `
        <div class="card-name">${p.name || 'Unknown'}</div>
        <div class="card-phone">${phone}</div>
        <div class="card-meta">${budget}${to}</div>
        ${lastMsg ? `<div class="card-last">${lastMsg}</div>` : ''}
      `;
      body.appendChild(card);
    });
  });
}

function openModal(phone) {
  currentPhone = phone;
  const p = allProspects[phone];
  document.getElementById('modal-name').textContent = p.name || 'Unknown';
  document.getElementById('modal-phone').textContent = phone;

  const stageLabel = STAGES.find(([c]) => c === p.stage)?.[1] || p.stage;
  const stageColor = STAGE_COLORS[p.stage] || '#aaa';
  document.getElementById('modal-meta').innerHTML = `
    <span class="meta-pill" style="background:${stageColor}22;color:${stageColor};border:1px solid ${stageColor}44">${stageLabel}</span>
    ${p.budget && p.budget !== 'unknown' ? `<span class="meta-pill" style="background:#1a3a1a;color:#4ade80;border:1px solid #2a5a2a">💰 ${p.budget}</span>` : ''}
    ${p.why && p.why !== 'unknown' ? `<span class="meta-pill" style="background:#1a1a3a;color:#818cf8;border:1px solid #2a2a5a">✨ ${p.why}</span>` : ''}
    ${p.updated_at ? `<span class="meta-pill" style="background:#1e1e1e;color:#555;border:1px solid #2a2a2a">🕐 ${p.updated_at}</span>` : ''}
  `;

  const convo = document.getElementById('modal-convo');
  convo.innerHTML = '';
  (p.conversation || []).forEach(msg => {
    const wrap = document.createElement('div');
    const isLead = msg.role === 'user';
    const isGavin = msg.role === 'gavin';
    wrap.className = 'msg-wrap' + (isLead ? '' : ' right');
    wrap.innerHTML = `
      <div class="msg-label">${isLead ? (p.name || 'Lead') : isGavin ? '👤 You' : '🤖 AI'}</div>
      <div class="msg ${isLead ? 'lead' : isGavin ? 'gavin' : 'ai'}">${msg.content}</div>
    `;
    convo.appendChild(wrap);
  });
  setTimeout(() => convo.scrollTop = convo.scrollHeight, 50);

  // Takeover state
  const btnTakeover = document.getElementById('btn-takeover');
  const replyArea = document.getElementById('reply-area');
  if (p.takeover) {
    btnTakeover.textContent = '🤖 Resume AI';
    btnTakeover.className = 'btn btn-resume';
    replyArea.classList.add('visible');
  } else {
    btnTakeover.textContent = '🙋 Take Over';
    btnTakeover.className = 'btn btn-takeover';
    replyArea.classList.remove('visible');
  }

  // Stage select
  const ss = document.getElementById('stage-select');
  ss.classList.remove('visible');
  ss.innerHTML = STAGES.map(([code, label]) =>
    `<span class="stage-opt" onclick="setStage('${phone}','${code}')">${label}</span>`
  ).join('');

  document.getElementById('modal').classList.add('open');
}

function closeModal() {
  document.getElementById('modal').classList.remove('open');
  document.getElementById('stage-select').classList.remove('visible');
  currentPhone = null;
}

async function toggleTakeover() {
  if (!currentPhone) return;
  const p = allProspects[currentPhone];
  const res = await fetch('/api/takeover', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ phone: currentPhone, takeover: !p.takeover })
  });
  await res.json();
  await loadPipeline();
  openModal(currentPhone);
}

async function sendManual() {
  if (!currentPhone) return;
  const text = document.getElementById('reply-text').value.trim();
  if (!text) return;
  const btn = document.getElementById('btn-send');
  btn.disabled = true;
  btn.textContent = 'Sending…';
  const res = await fetch('/api/send', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ phone: currentPhone, message: text })
  });
  const data = await res.json();
  if (data.ok) {
    document.getElementById('reply-text').value = '';
    await loadPipeline();
    openModal(currentPhone);
  } else {
    alert('Failed to send: ' + (data.error || 'unknown error'));
  }
  btn.disabled = false;
  btn.textContent = 'Send ↑';
}

function toggleStageSelect() {
  document.getElementById('stage-select').classList.toggle('visible');
}

async function setStage(phone, stage) {
  await fetch('/api/set_stage', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ phone, stage })
  });
  document.getElementById('stage-select').classList.remove('visible');
  await loadPipeline();
  openModal(phone);
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
document.getElementById('modal').addEventListener('click', e => { if (e.target === document.getElementById('modal')) closeModal(); });

loadPipeline();
setInterval(loadPipeline, 30000); // auto-refresh every 30s
</script>
</body>
</html>
"""

# ============================================================
# DASHBOARD ROUTE
# ============================================================
@app.route("/dashboard")
def dashboard():
    import json as _json
    stages_json = _json.dumps(STAGES)
    colors_json = _json.dumps(STAGE_COLORS)
    html = DASHBOARD_HTML.replace("{{ stages_json }}", stages_json).replace("{{ colors_json }}", colors_json)
    return html

# ============================================================
# API — PROSPECTS
# ============================================================
@app.route("/api/prospects")
def api_prospects():
    return jsonify({
        "total": len(prospects),
        "call_ready": sum(1 for p in prospects.values() if p.get("stage") == "CALL_READY"),
        "needs_gavin": sum(1 for p in prospects.values() if p.get("stage") == "NEEDS_GAVIN"),
        "prospects": prospects
    })

# ============================================================
# API — TAKEOVER TOGGLE
# ============================================================
@app.route("/api/takeover", methods=["POST"])
def api_takeover():
    data = request.json
    phone = data.get("phone")
    takeover = data.get("takeover", True)
    if phone in prospects:
        prospects[phone]["takeover"] = takeover
        save_data(prospects)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Not found"}), 404

# ============================================================
# API — MANUAL SEND (Gavin takeover reply)
# ============================================================
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
            prospects[phone]["conversation"].append({"role": "gavin", "content": message})
            prospects[phone]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            save_data(prospects)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ============================================================
# API — SET STAGE MANUALLY
# ============================================================
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

# ============================================================
# TWILIO WEBHOOK
# ============================================================
@app.route("/sms", methods=["POST"])
def sms_reply():
    incoming_msg = request.form.get("Body", "").strip()
    from_number  = request.form.get("From", "").strip()
    print(f"\n📩 {from_number}: {incoming_msg}")

    if from_number not in prospects:
        prospects[from_number] = {"name": "Unknown", "stage": "NEW", "budget": "unknown",
                                   "why": "unknown", "last_message": "", "updated_at": "",
                                   "takeover": False, "conversation": []}

    prospects[from_number]["last_message"] = incoming_msg
    prospects[from_number]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    prospects[from_number]["conversation"].append({"role": "user", "content": incoming_msg})

    # If Gavin has taken over, don't auto-respond
    if prospects[from_number].get("takeover"):
        save_data(prospects)
        print(f"⏸️  Takeover active for {from_number} — no AI reply sent")
        return ('', 204)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=prospects[from_number]["conversation"]
        )
        raw_reply = response.content[0].text.strip()
    except Exception as e:
        print(f"❌ Claude error: {e}")
        raw_reply = "Hey, something came up on my end — I'll follow up with you shortly!"

    clean_reply, stage, budget, why = parse_stage(raw_reply)

    if stage and any(s[0] == stage for s in STAGES):
        prospects[from_number]["stage"] = stage
    if budget and budget != "unknown":
        prospects[from_number]["budget"] = budget
    if why and why != "unknown":
        prospects[from_number]["why"] = why

    prospects[from_number]["conversation"].append({"role": "assistant", "content": clean_reply})
    save_data(prospects)

    print(f"🤖 {clean_reply}")
    
    resp = MessagingResponse()
    resp.message(clean_reply)
    return str(resp)

# ============================================================
# HEALTH
# ============================================================
@app.route("/")
def health():
    return f"✅ Running | {len(prospects)} prospects | <a href='/dashboard'>Open Dashboard</a>", 200

# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    print("🚀 Server starting on http://localhost:5000")
    print("📊 Dashboard: http://localhost:5000/dashboard")
    print(f"💾 {len(prospects)} prospects loaded")
    app.run(host="0.0.0.0", port=5000, debug=False)
