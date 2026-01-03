import os
from datetime import datetime
from fastapi import FastAPI, Request
from telegram import Bot
from supabase import create_client
from openai import OpenAI

# ================= ENV =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ================= CLIENTS =================
bot = Bot(token=TELEGRAM_TOKEN)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# ================= EMBEDDINGS =================
def embed(text: str):
    res = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return res.data[0].embedding

# ================= MEMORY SEARCH =================
def search_memory(text: str):
    try:
        emb = embed(text)
        res = supabase.rpc(
            "match_memories",
            {
                "query_embedding": emb,
                "match_threshold": 0.65,
                "match_count": 5
            }
        ).execute()
        return res.data or []
    except Exception:
        return []

# ================= DATE / TIME =================
def handle_date_time(text: str):
    t = text.lower()

    if "today" in t or "date" in t:
        return datetime.now().strftime("Today is %B %d, %Y.")

    if "time" in t or "now" in t:
        return datetime.now().strftime("The current time is %I:%M %p.")

    return None

# ================= INTENT HELPERS =================
def needs_memory(text: str):
    triggers = ["when", "what", "which", "did i", "last", "earlier", "remember"]
    return any(t in text.lower() for t in triggers)

def is_pending_query(text: str):
    return "any pending" in text.lower() or "pendings" in text.lower()

def is_agenda_statement(text: str):
    triggers = ["today", "after", "tonight", "tomorrow", "have to", "focus on"]
    return any(t in text.lower() for t in triggers) and "remind" not in text.lower()

def is_reminder(text: str):
    return "remind me" in text.lower()

def is_correction(text: str):
    triggers = ["was", "actually", "not", "instead", "on"]
    return any(t in text.lower() for t in triggers)

# ================= AI DECISION =================
def ai_reply(user_text: str, memories: list):
    memory_context = "\n".join(
        f"- {m['content']}" for m in memories if not m.get("is_override")
    )

    system_prompt = f"""
You are a personal AI assistant for one person.

RULES:
- If memory context exists, use it
- Prefer MOST RECENT memory
- If a correction is stated, accept the newer fact
- Do NOT list multiple conflicting answers
- Answer cleanly and confidently

MEMORY CONTEXT:
{memory_context if memory_context else "None"}
"""

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ],
        temperature=0.2
    )

    return res.choices[0].message.content.strip()

# ================= WEBHOOK =================
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")

    if not chat_id or not text:
        return {"ok": True}

    text = text.strip()

    # 1️⃣ Fast date/time
    quick = handle_date_time(text)
    if quick:
        await bot.send_message(chat_id=chat_id, text=quick)
        return {"ok": True}

    # 2️⃣ Pending query (NO embeddings)
    if is_pending_query(text):
        await bot.send_message(
            chat_id=chat_id,
            text="I’ll check your reminders and agenda. Nothing pending right now."
        )
        return {"ok": True}

    # 3️⃣ Agenda statement
    if is_agenda_statement(text):
        emb = embed(text)
        supabase.table("memories").insert({
            "content": text,
            "category": "work_antler",
            "embedding": emb,
            "is_override": False
        }).execute()

        await bot.send_message(
            chat_id=chat_id,
            text="Got it. I’ve noted this in your agenda."
        )
        return {"ok": True}

    # 4️⃣ Reminder
    if is_reminder(text):
        emb = embed(text)
        supabase.table("memories").insert({
            "content": text,
            "category": "reminder",
            "embedding": emb,
            "is_override": False
        }).execute()

        await bot.send_message(
            chat_id=chat_id,
            text="I’ll remind you as requested."
        )
        return {"ok": True}

    # 5️⃣ Memory-based question
    memories = []
    if needs_memory(text):
        memories = search_memory(text)

    reply = ai_reply(text, memories)

    # 6️⃣ Correction handling
    if memories and is_correction(text):
        emb = embed(text)
        supabase.table("memories").insert({
            "content": text,
            "category": "general",
            "embedding": emb,
            "is_override": True
        }).execute()

    await bot.send_message(chat_id=chat_id, text=reply)
    return {"ok": True}
