import os
import re
from datetime import datetime, date, timedelta
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

# ================= DATE PARSER =================
def extract_date(text: str) -> date | None:
    t = text.lower()

    if "today" in t:
        return date.today()

    if "tomorrow" in t:
        return date.today() + timedelta(days=1)

    if "monday" in t:
        return date.today() + timedelta(days=(7 - date.today().weekday()) % 7)

    match = re.search(r"(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", t)
    if match:
        day = int(match.group(1))
        month = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
        }[match.group(2)]
        return date(date.today().year, month, day)

    return None

# ================= INTENT HELPERS =================
def is_add_reminder(text: str):
    return any(x in text.lower() for x in [
        "remind me", "add reminder", "set reminder"
    ])

def is_list_reminders(text: str):
    return any(x in text.lower() for x in [
        "upcoming reminders", "pending reminders",
        "what reminders", "any reminders"
    ])

def should_store_note(text: str):
    return any(x in text.lower() for x in [
        "i went", "i met", "i need to", "i have to",
        "i did", "i was", "follow up", "meeting",
        "onboarding", "worked on", "spoke with"
    ])

def is_memory_recall(text: str):
    return any(x in text.lower() for x in [
        "when did", "what did", "anything regarding",
        "notes about", "tell me about", "regarding"
    ])

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
    except Exception as e:
        print("MEMORY SEARCH ERROR:", e)
        return []

# ================= AI ANSWER =================
def ai_answer(user_text: str, memories: list):
    context = "\n".join(f"- {m['content']}" for m in memories)

    prompt = f"""
You are a personal assistant with memory.

Rules:
- If memory exists, answer from it
- If not, say you don’t have a record yet
- Be concise

Memory:
{context if context else "No matching memory"}
"""

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_text}
        ],
        temperature=0.2
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

    # ---------- ADD REMINDER ----------
    if is_add_reminder(text):
        due = extract_date(text)
        if not due:
            await bot.send_message(chat_id, "Please tell me the date for the reminder.")
            return {"ok": True}

        try:
            supabase.table("reminders").insert({
                "user_id": str(chat_id),
                "title": text,
                "due_date": due.isoformat()
            }).execute()
            await bot.send_message(chat_id, f"Reminder added for {due.strftime('%d %b')}.")
        except Exception as e:
            print("REMINDER ERROR:", e)
            await bot.send_message(chat_id, "I couldn’t save the reminder.")
        return {"ok": True}

    # ---------- LIST REMINDERS ----------
    if is_list_reminders(text):
        today = date.today().isoformat()
        res = supabase.table("reminders") \
            .select("*") \
            .eq("user_id", str(chat_id)) \
            .eq("completed", False) \
            .gte("due_date", today) \
            .order("due_date") \
            .execute()

        reminders = res.data or []
        if not reminders:
            await bot.send_message(chat_id, "You don’t have any upcoming reminders.")
            return {"ok": True}

        lines = ["Here are your upcoming reminders:"]
        for r in reminders:
            d = datetime.fromisoformat(r["due_date"]).strftime("%d %b")
            lines.append(f"- {d}: {r['title']}")

        await bot.send_message(chat_id, "\n".join(lines))
        return {"ok": True}

    # ---------- STORE NOTE ----------
    if should_store_note(text):
        try:
            supabase.table("memories").insert({
                "content": text,
                "category": "note",
                "embedding": embed(text)
            }).execute()
            await bot.send_message(chat_id, "Got it. I’ve noted this.")
        except Exception as e:
            print("NOTE ERROR:", e)
            await bot.send_message(chat_id, "I heard you, but couldn’t save it.")
        return {"ok": True}

    # ---------- RECALL MEMORY ----------
    if is_memory_recall(text):
        memories = search_memory(text)
        reply = ai_answer(text, memories)
        await bot.send_message(chat_id, reply)
        return {"ok": True}

    # ---------- NORMAL CHAT ----------
    await bot.send_message(
        chat_id,
        "I can remember notes, set reminders, and recall past info. Just tell me naturally."
    )
    return {"ok": True}
