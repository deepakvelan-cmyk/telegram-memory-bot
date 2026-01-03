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
    return client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    ).data[0].embedding

# ================= MEMORY SEARCH =================
def search_memory(text: str):
    try:
        emb = embed(text)
        res = supabase.rpc(
            "match_memories",
            {
                "query_embedding": emb,
                "match_threshold": 0.6,
                "match_count": 5
            }
        ).execute()
        return res.data or []
    except:
        return []

# ================= DATE / TIME =================
def handle_date_time(text: str):
    t = text.lower()
    if "today" in t or "date" in t:
        return datetime.now().strftime("Today is %B %d, %Y.")
    if "time" in t or "now" in t:
        return datetime.now().strftime("The current time is %I:%M %p.")
    return None

# ================= INTENT =================
def is_past_fact_question(text: str):
    t = text.lower()
    return (
        "when" in t
        or "what" in t
        or "did i" in t
        or "went" in t
        or "had" in t
        or "issue" in t
    )

def is_pending_query(text: str):
    t = text.lower()
    return "pending" in t or "tasks" in t or "reminder" in t

def is_reminder(text: str):
    return "remind me" in text.lower()

def is_agenda(text: str):
    t = text.lower()
    return any(x in t for x in ["today", "tomorrow", "after", "tonight", "focus on"]) and "remind" not in t

# ================= AI ANSWER =================
def ai_answer(user_text: str, memories: list):
    memory_context = "\n".join(f"- {m['content']}" for m in memories)

    system_prompt = f"""
You are a personal AI assistant with persistent memory.

ABSOLUTE RULES:
- You DO have memory.
- If memory context exists, you MUST use it.
- You are NOT allowed to say you have no memory.
- If nothing is found, say "I don’t find anything recorded yet."

MEMORY:
{memory_context if memory_context else "No matching memory found"}
"""

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ],
        temperature=0.1
    )

    return res.choices[0].message.content.strip()

# ================= WEBHOOK =================
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    msg = data.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text")

    if not chat_id or not text:
        return {"ok": True}

    text = text.strip()

    # Fast date/time
    quick = handle_date_time(text)
    if quick:
        await bot.send_message(chat_id, quick)
        return {"ok": True}

    # Pending check (STATE, NOT MEMORY)
    if is_pending_query(text):
        await bot.send_message(chat_id, "You don’t have any pending reminders or tasks right now.")
        return {"ok": True}

    # Agenda
    if is_agenda(text):
        supabase.table("memories").insert({
            "content": text,
            "category": "work_antler",
            "embedding": embed(text),
            "is_override": False
        }).execute()
        await bot.send_message(chat_id, "Got it. I’ve noted this.")
        return {"ok": True}

    # Reminder
    if is_reminder(text):
        supabase.table("memories").insert({
            "content": text,
            "category": "reminder",
            "embedding": embed(text),
            "is_override": False
        }).execute()
        await bot.send_message(chat_id, "Reminder saved.")
        return {"ok": True}

    # Past fact recall
    memories = []
    if is_past_fact_question(text):
        memories = search_memory(text)

    reply = ai_answer(text, memories)

    # Store declarative facts
    if not is_past_fact_question(text):
        supabase.table("memories").insert({
            "content": text,
            "category": "general",
            "embedding": embed(text),
            "is_override": False
        }).execute()

    await bot.send_message(chat_id, reply)
    return {"ok": True}
