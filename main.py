import os
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

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
openai_client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# ================= LOAD CONFIG =================
with open("assistant_config.json", "r") as f:
    CONFIG = json.load(f)

MEMORY_RULES = CONFIG.get("MEMORY_RULES", [])
PEOPLE = CONFIG.get("PEOPLE", [])
WORK_CONTEXT = CONFIG.get("WORK_CONTEXT", [])

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# ================= TIME HELPERS =================
def now_utc():
    return datetime.now(tz=UTC)

def now_ist_human():
    return datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")

# ================= UTILITIES =================
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())

def embed(text: str):
    res = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return res.data[0].embedding

# ================= RULE ENGINE =================
def match_memory_rule(text: str):
    for rule in MEMORY_RULES:
        if rule["pattern"].lower() in text:
            return rule["action"], rule.get("category", "auto")
    return None, None

def resolve_category(text: str):
    for p in PEOPLE:
        if p["name"].lower() in text:
            return p.get("domain", "personal")

    for w in WORK_CONTEXT:
        if w["topic"].lower() in text:
            return "work"

    return "personal"

def extract_last_n(text: str):
    match = re.search(r"last\s+(\d+)", text)
    return int(match.group(1)) if match else None

# ================= STORAGE =================
def store_memory(raw_text: str, category: str):
    supabase.table("memories").insert({
        "raw_text": raw_text,
        "normalized_text": normalize(raw_text),
        "category": category,
        "timestamp_utc": now_utc().isoformat(),
        "timestamp_human": now_ist_human(),
        "embedding": embed(raw_text),
        "metadata": {}
    }).execute()

# ================= RECALL =================
def recall_memories(query: str, limit: int | None = None):
    q = normalize(query)

    req = supabase.table("memories") \
        .select("*") \
        .ilike("normalized_text", f"%{q}%") \
        .order("timestamp_utc", desc=True)

    if limit:
        req = req.limit(limit)

    return req.execute().data or []

# ================= WEBHOOK =================
@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    msg = payload.get("message", {})

    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text")

    if not chat_id or not text:
        return {"ok": True}

    raw_text = text.strip()
    norm_text = normalize(raw_text)

    # ---------- RULE MATCH ----------
    action, rule_category = match_memory_rule(norm_text)

    # ---------- RECALL (LAST N TIMES) ----------
    if action == "recall_memory" or "last" in norm_text:
        n = extract_last_n(norm_text)
        memories = recall_memories(norm_text, n)

        if not memories:
            await bot.send_message(chat_id, "I don’t have any record of that yet.")
            return {"ok": True}

        response = []
        for i, m in enumerate(memories, 1):
            response.append(f"{i}. {m['timestamp_human']} – {m['raw_text']}")

        await bot.send_message(chat_id, "\n".join(response))
        return {"ok": True}

    # ---------- STORE MEMORY ----------
    if action == "store_memory":
        category = resolve_category(norm_text) if rule_category == "auto" else rule_category
        store_memory(raw_text, category)
        await bot.send_message(chat_id, "Noted.")
        return {"ok": True}

    # ---------- AUTO LINK STORAGE ----------
    if "http://" in norm_text or "https://" in norm_text or "www." in norm_text:
        store_memory(raw_text, "link")
        await bot.send_message(chat_id, "Link saved.")
        return {"ok": True}

    # ---------- DEFAULT RESPONSE ----------
    await bot.send_message(
        chat_id,
        "I’m here. I can store memories, recall past events with dates, and keep links or notes."
    )

    return {"ok": True}
