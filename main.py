import os
from datetime import datetime
from fastapi import FastAPI, Request
from telegram import Bot
from supabase import create_client

# ================= CONFIG =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

# ================= HELPERS =================

def now_human():
    return datetime.now().strftime("%d %b %Y, %I:%M %p IST")


def is_question(text: str) -> bool:
    triggers = [
        "what", "when", "any", "did i", "do i have",
        "tell me", "show me", "pending", "issues", "problems"
    ]
    t = text.lower()
    return any(k in t for k in triggers)


def extract_keywords(text: str):
    stopwords = {
        "when", "did", "i", "have", "any", "is", "was",
        "the", "a", "an", "tell", "me", "about",
        "what", "show", "my", "please"
    }
    return [
        w for w in text.lower().split()
        if w not in stopwords and len(w) > 2
    ]


def store_memory(user_id: str, text: str):
    supabase.table("memories").insert({
        "user_id": user_id,
        "content": text,
        "timestamp_human": now_human()
    }).execute()


def recall_memories(user_id: str, text: str, limit: int = 5):
    keywords = extract_keywords(text)

    if not keywords:
        return []

    query = (
        supabase
        .table("memories")
        .select("content, timestamp_human")
        .eq("user_id", user_id)
    )

    for kw in keywords:
        query = query.ilike("content", f"%{kw}%")

    res = (
        query
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )

    return res.data or []

# ================= WEBHOOK =================

@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    msg = payload.get("message", {})

    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text")

    if not chat_id or not text:
        return {"ok": True}

    text = text.strip()
    user_id = str(chat_id)

    # ---------- RECALL ----------
    if is_question(text):
        memories = recall_memories(user_id, text)

        if not memories:
            await bot.send_message(chat_id, "I don’t have any record of that yet.")
            return {"ok": True}

        reply = "Here’s what I have:\n"
        for m in memories:
            reply += f"- {m['timestamp_human']}: {m['content']}\n"

        await bot.send_message(chat_id, reply)
        return {"ok": True}

    # ---------- STORE ----------
    store_memory(user_id, text)
    await bot.send_message(chat_id, "Noted.")
    return {"ok": True}
