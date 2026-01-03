import os
import re
from datetime import datetime, date
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

# ================= DATE PARSER =================
def extract_date(text: str) -> date | None:
    text = text.lower()

    if "today" in text:
        return date.today()

    if "tomorrow" in text:
        return date.today().fromordinal(date.today().toordinal() + 1)

    match = re.search(r"(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", text)
    if match:
        day = int(match.group(1))
        month_str = match.group(2)
        month_map = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
        }
        month = month_map[month_str]
        year = date.today().year
        return date(year, month, day)

    return None

# ================= INTENT =================
def is_add_reminder(text: str) -> bool:
    return any(x in text.lower() for x in [
        "remind me", "add reminder", "set reminder"
    ])

def is_list_reminders(text: str) -> bool:
    return any(x in text.lower() for x in [
        "upcoming reminders", "pending reminders", "what reminders", "any reminders"
    ])

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
            await bot.send_message(
                chat_id,
                "Please tell me the date for the reminder."
            )
            return {"ok": True}

        supabase.table("reminders").insert({
            "user_id": str(chat_id),
            "title": text,
            "due_date": due.isoformat()
        }).execute()

        await bot.send_message(
            chat_id,
            f"Reminder added for {due.strftime('%d %b')}."
        )
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
            await bot.send_message(
                chat_id,
                "You don’t have any upcoming reminders."
            )
            return {"ok": True}

        lines = ["Here are your upcoming reminders:"]
        for r in reminders:
            d = datetime.fromisoformat(r["due_date"]).strftime("%d %b")
            lines.append(f"- {d}: {r['title']}")

        await bot.send_message(chat_id, "\n".join(lines))
        return {"ok": True}

    # ---------- DEFAULT CHAT ----------
    await bot.send_message(
        chat_id,
        "I can add reminders or list upcoming ones. For example: “Remind me to pay rent on 5 Jan”."
    )
    return {"ok": True}
