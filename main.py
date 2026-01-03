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
                "match_threshold": 0.6,
                "match_count": 6
            }
        ).execute()
        return res.data or []
    except Exception as e:
        print("memory search error:", e)
        return []

# ================= DATE / TIME =================
def handle_date_time(text: str):
    t = text.lower()
    if "today" in t or "date" in t:
        return datetime.now().strftime("Today is %B %d, %Y.")
    if "time" in t or "now" in t:
        return datetime.now().strftime("The current time is %I:%M %p.")
    return None

# ================= UNIVERSAL INTENT PARSER =================
def parse_intent(text: str):
    t = text.lower().strip()

    intent = {
        "type": "chat",     # chat | recall | reminder | agenda | pending
        "entity": None
    }

    if any(x in t for x in [
        "remind me", "add reminder", "set reminder", "add a reminder"
    ]):
        intent["type"] = "reminder"

    elif any(x in t for x in [
        "any pending", "pending tasks", "pending reminders", "what's pending"
    ]):
        intent["type"] = "pending"

    elif any(x in t for x in [
        "today", "tomorrow", "tonight", "after", "focus on", "i have to"
    ]) and "remind" not in t:
        intent["type"] = "agenda"

    elif any(x in t for x in [
        "when", "what", "did i", "tell me", "anything",
        "regarding", "about", "notes"
    ]):
        intent["type"] = "recall"

    words = [w for w in t.split() if len(w) > 2]
    if words:
        intent["entity"] = words[-1]

    return intent

# ================= AI ANSWER =================
def ai_answer(user_text: str, memories: list):
    memory_context = "\n".join(f"- {m['content']}" for m in memories)

    system_prompt = f"""
You are a personal AI assistant with persistent memory.

RULES:
- You DO have memory.
- If memory exists, use it.
- Never say you have no memory.
- If nothing matches, say: "I don’t see a matching record yet."

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

    # 1️⃣ Fast date / time
    quick = handle_date_time(text)
    if quick:
        await bot.send_message(chat_id, quick)
        return {"ok": True}

    intent = parse_intent(text)

    # 2️⃣ Pending
    if intent["type"] == "pending":
        await bot.send_message(
            chat_id,
            "You don’t have any pending reminders or tasks right now."
        )
        return {"ok": True}

    # 3️⃣ Reminder
    if intent["type"] == "reminder":
        supabase.table("memories").insert({
            "content": text,
            "category": "reminder",
            "embedding": embed(text)
        }).execute()

        await bot.send_message(chat_id, "Reminder added.")
        return {"ok": True}

    # 4️⃣ Agenda / Notes
    if intent["type"] == "agenda":
        supabase.table("memories").insert({
            "content": text,
            "category": "agenda",
            "embedding": embed(text)
        }).execute()

        await bot.send_message(chat_id, "Noted.")
        return {"ok": True}

    # 5️⃣ Recall
    memories = []
    if intent["type"] == "recall":
        memories = search_memory(text)

    reply = ai_answer(text, memories)

    # 6️⃣ Always store personal facts
    if any(x in text.lower() for x in [
        "i had", "i have", "i went", "i faced", "i did",
        "issue", "problem"
    ]):
        supabase.table("memories").insert({
            "content": text,
            "category": "fact",
            "embedding": embed(text)
        }).execute()

    await bot.send_message(chat_id, reply)
    return {"ok": True}
