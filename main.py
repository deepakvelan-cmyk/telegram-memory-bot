import os
import re
from fastapi import FastAPI, Request
from telegram import Bot
from supabase import create_client
from openai import OpenAI

# ================== CONFIG ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# ================== RULE SET ==================
HIGH_PRIORITY_NAMES = {"nirbhay", "ns"}

SENSITIVE_KEYWORDS = {
    "dimpu", "dimple", "santoshi", "anudeep",
    "bala", "niva", "dad",
    "pill", "pills", "medicine", "tablet", "vitamins"
}

WORK_KEYWORDS = {
    "signup", "onboarding", "antler", "void check",
    "vaayu", "vaayu scrubs", "client", "meeting",
    "design", "website", "churn", "churned",
    "call", "follow up", "review", "deployment"
}

TASK_TRIGGERS = {
    "remember", "remind", "need to", "have to",
    "must", "pay", "renew", "follow up", "call"
}

URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")

# ================== HELPERS ==================
def embed(text: str):
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding


def categorize(text: str):
    t = text.lower()

    # Priority (hard rule)
    priority = "high" if any(n in t for n in HIGH_PRIORITY_NAMES) else "normal"

    # Domain (hard → soft)
    if any(k in t for k in SENSITIVE_KEYWORDS):
        domain = "sensitive"
    elif any(k in t for k in WORK_KEYWORDS):
        domain = "work"
    else:
        domain = "personal"

    # Type
    if any(k in t for k in TASK_TRIGGERS):
        record_type = "task"
    else:
        record_type = "memory"

    # Links override type
    links = URL_PATTERN.findall(text)
    if links:
        record_type = "link"

    return {
        "domain": domain,
        "priority": priority,
        "type": record_type,
        "links": links
    }


def search_memory(query_embedding):
    result = supabase.rpc(
        "match_memories",
        {
            "query_embedding": query_embedding,
            "match_threshold": 0.75,
            "match_count": 5
        }
    ).execute()
    return result.data or []


# ================== WEBHOOK ==================
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()

    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")

    if not chat_id or not text:
        return {"ok": True}

    text = text.strip()

    # ---------- QUESTIONS (search) ----------
    if "?" in text.lower():
        try:
            emb = embed(text)
            memories = search_memory(emb)

            if not memories:
                reply = "I don’t have anything relevant yet."
            else:
                lines = [f"- {m['content']}" for m in memories]
                reply = "Here’s what I found:\n" + "\n".join(lines)

        except Exception as e:
            print("Search error:", e)
            reply = "I’m running, but my AI quota is temporarily unavailable."

    # ---------- STORAGE ----------
    else:
        if len(text) < 6:
            reply = "Hey 🙂 I’m here."
        else:
            try:
                meta = categorize(text)
                emb = embed(text)

                supabase.table("memories").insert({
                    "content": text,
                    "domain": meta["domain"],
                    "priority": meta["priority"],
                    "type": meta["type"],
                    "links": meta["links"],
                    "status": "open" if meta["type"] == "task" else None,
                    "embedding": emb
                }).execute()

                reply = "Got it — I’ll remember that."

            except Exception as e:
                print("Insert error:", e)
                reply = "I couldn’t save that right now, but I’m still here."

    await bot.send_message(chat_id=chat_id, text=reply)
    return {"ok": True}
