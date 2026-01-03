import os
import re
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from telegram import Bot
from supabase import create_client

# ---------- ENV ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

IST = timezone(timedelta(hours=5, minutes=30))


# ---------- HELPERS ----------

def ist_now_human():
    return datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def is_question(text: str) -> bool:
    return text.startswith(("when", "what", "did", "tell", "show", "how"))


def extract_last_n(text: str) -> int:
    match = re.search(r"last\s+(\d+)", text)
    return int(match.group(1)) if match else 5


def extract_topic(text: str) -> str:
    stop = ["when did i", "tell me", "what is", "show me", "did i"]
    for s in stop:
        text = text.replace(s, "")
    return text.strip()


# ---------- STORAGE ----------

def store_memory(user_id: str, raw_text: str):
    supabase.table("memories").insert({
        "user_id": user_id,
        "content": raw_text,          # 🔴 REQUIRED COLUMN
        "raw_text": raw_text,
        "source": "telegram",
        "timestamp_human": ist_now_human()
    }).execute()


def recall_memories(user_id: str, topic: str, limit: int):
    res = supabase.table("memories") \
        .select("raw_text, timestamp_human") \
        .eq("user_id", user_id) \
        .ilike("raw_text", f"%{topic}%") \
        .order("created_at", desc=True) \
        .limit(limit) \
        .execute()
    return res.data or []


# ---------- WEBHOOK ----------

@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    msg = payload.get("message", {})

    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text")

    if not chat_id or not text:
        return {"ok": True}

    raw_text = text.strip()
    norm = normalize(raw_text)

    # ---------- STORE ----------
    if not is_question(norm):
        store_memory(str(chat_id), raw_text)
        await bot.send_message(chat_id, "Noted.")
        return {"ok": True}

    # ---------- RECALL ----------
    n = extract_last_n(norm)
    topic = extract_topic(norm)

    memories = recall_memories(str(chat_id), topic, n)

    if not memories:
        await bot.send_message(chat_id, "I don’t have any record of that yet.")
        return {"ok": True}

    lines = [
        f"{i}. {m['timestamp_human']} — {m['raw_text']}"
        for i, m in enumerate(memories, 1)
    ]

    await bot.send_message(chat_id, "\n".join(lines))
    return {"ok": True}
