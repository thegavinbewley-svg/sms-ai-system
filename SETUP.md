# SMS AI System — Setup Guide
## Built for Gavin | Nathan Nazareth Dropshipping Program

---

## What This Does
1. **blast.py** — Reads your leads CSV and sends a personalized opener SMS to each lead
2. **server.py** — Runs a webhook server that receives replies and automatically responds using AI trained on Zoltan's script

---

## STEP 1 — Install Dependencies

Open Terminal and run:
```bash
pip3 install twilio flask anthropic
```

---

## STEP 2 — Get Your Anthropic API Key

1. Go to https://console.anthropic.com
2. Click "API Keys" → "Create Key"
3. Copy the key (starts with `sk-ant-...`)

---

## STEP 3 — Set Your Anthropic API Key

In Terminal, run:
```bash
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
```

To make it permanent, add that line to your `~/.zshrc` file.

---

## STEP 4 — Prepare Your Leads CSV

Format your CSV like this (column names must match exactly):
```
First Name,Last Name,Phone
John,Smith,+12505551234
Sarah,Johnson,7785559876
```

Phone numbers can be with or without +1 — the script handles both.

Save it as `leads.csv` in the same folder as the scripts.

If your CSV has different column names, open `blast.py` and update these lines:
```python
FIRST_NAME_COL = "First Name"   # Change to match your CSV
PHONE_COL      = "Phone"        # Change to match your CSV
```

---

## STEP 5 — Run the Blast

Navigate to the folder in Terminal:
```bash
cd ~/Desktop/sms-ai-system
python3 blast.py
```

You'll see live output as each message sends.

---

## STEP 6 — Set Up the Webhook (So AI Can Reply)

You need to expose your local server to the internet. Use **ngrok** (free):

1. Download ngrok: https://ngrok.com/download
2. Run your server:
```bash
python3 server.py
```
3. In a NEW Terminal tab, run:
```bash
ngrok http 5000
```
4. Copy the https URL it gives you (looks like `https://abc123.ngrok.io`)

---

## STEP 7 — Connect Webhook to Twilio

1. Go to Twilio Console → Phone Numbers → Active Numbers → +12362432623
2. Scroll to **Messaging Configuration**
3. Under "A message comes in" → set to **Webhook**
4. Paste your ngrok URL + `/sms` → example: `https://abc123.ngrok.io/sms`
5. Set HTTP to **HTTP POST**
6. Click **Save Configuration**

Now when a lead replies, Twilio sends it to your server, Claude responds, and the reply goes back automatically.

---

## STEP 8 — Test It

Send a text to your Twilio number (+1 236-243-2623) from your personal phone.
You should get an AI response within seconds.

---

## CONVERSATION FLOW THE AI FOLLOWS

```
Lead replies YES/interested
        ↓
AI asks: "What made you interested in dropshipping?"
        ↓
AI asks: "Could you put aside 1-2 hours a day?"
        ↓
AI asks: "How much could you comfortably invest to get started?"
        ↓
AI pitches the call with Nathan's business partner (Evan)
        ↓
QUALIFIED → "I'll connect you with Evan"
SKEPTICAL → "Let me have Gavin jump on a quick call with you first"
```

---

## IMPORTANT NOTES

- **ngrok URL changes every time you restart it** — you'll need to update the Twilio webhook each session unless you pay for a static ngrok URL ($8/month)
- **Conversation memory is stored in RAM** — if you restart the server, conversation history resets
- **Trial Twilio account** — you can only send to verified numbers until you upgrade
- **Upgrade Twilio** when ready to go live with real leads ($15 credit gets you started)

---

## FILES IN THIS FOLDER

| File | Purpose |
|------|---------|
| `blast.py` | Send opener SMS to all leads in CSV |
| `server.py` | Webhook server + AI auto-responder |
| `leads.csv` | Your leads (replace with real data) |
| `SETUP.md` | This guide |

---

## SUPPORT

If anything breaks, the most common issues are:
1. Missing API key → make sure `ANTHROPIC_API_KEY` is set
2. Wrong CSV column names → check `FIRST_NAME_COL` and `PHONE_COL` in blast.py
3. Twilio webhook not set → follow Step 7 again
4. ngrok not running → server.py must be running AND ngrok must be running simultaneously

---

## CHECKING YOUR PIPELINE (Morning Briefing)

While your server is running, open a browser and go to:
```
http://localhost:5000/prospects
```

You'll see every lead grouped by stage — who's ready for Evan, who needs your call, who's dead.

**OR** — just ask me here in Claude chat each morning:
> "Pull up today's prospects"

And I'll read the prospects.json file and give you a clean breakdown.

---

## PROSPECT STAGES

| Stage | Meaning |
|-------|---------|
| NEW | Opener sent, no reply yet |
| INTERESTED | Confirmed yes to dropshipping |
| QUALIFIED_WHY | Gave their motivation |
| QUALIFIED_TIME | Confirmed 1-2 hrs/day |
| QUALIFIED_BUDGET | Gave a dollar amount |
| CALL_READY ✅ | Ready for Evan — hot lead |
| NEEDS_GAVIN 📞 | Skeptical, get Gavin on first |
| SCHEDULED 📅 | Call time confirmed |
| DEAD ❌ | No money / not interested |

---

## OFF-TRACK FALLBACK

If a lead starts going in circles or asking too many questions the AI can't cleanly answer, it will automatically say:
> "Honestly it's way easier for me to just explain everything on a quick call — are you free to hop on one now?"

This resets the conversation and pushes them to a call.
