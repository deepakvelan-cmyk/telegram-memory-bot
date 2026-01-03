import os
from datetime import datetime
from fastapi import FastAPI, Request
from telegram import Bot
from supabase import create_client
from openai import OpenAI

# ---------------- ENV ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ---------------- CLIENTS ----------------
bot = Bot(token=TELEGRAM_TOKEN)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# ---------------- EMBEDDINGS ----------------
def embed(text: str):
    res = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return res.data[0].embedding

# ---------------- MEMORY SEARCH ----------------
def search_memory(text: str):
    try:
        emb = embed(text)
        res = supabase.rpc(
            "match_memories",
            {
                "query_embedding": emb,
                "match_threshold": 0.65,
                "match_count": 6
            }
        ).execute()
        return res.data or []
    except Exception:
        return []

# ---------------- DATE / TIME ----------------
def handle_date_time(text: str):
    t = text.lower()

    if "date" in t or "today" in t:
        return datetime.now().strftime("Today is %B %d, %Y.")

    if "time" in t or "now" in t:
        return datetime.now().strftime("The current time is %I:%M %p.")

    return None

# ---------------- AI DECISION BRAIN ----------------
def ai_decide(user_text: str, memories: list):
    memory_context = "\n".join(
        f"- {m['content']} (category: {m.get('category','general')})"
        for m in memories
    )

    system_prompt = f"""
You are the ONLY AI assistant for one person.

You think, decide, remember, and evolve.

STRICT CATEGORIES:
- high_priority
- personal_secure
- work_antler
- reminder
- link
- general

RULES:
• Nirbhay, NS → high_priority
• Dimpu, Dimple, Santoshi, Anudeep, Bala, Niva, Dad, pills, medicine → personal_secure
• signup, onboarding, antler, void check, vaayu scrubs, client, meeting, design, website, churn → work_antler
• URLs → link
• Any future intent → reminder
• Questions alone → do NOT store
• Events, reminders, tasks → store
• Never refuse storing reminders
• Be human and concise

PAST MEMORY CONTEXT:
{memory_context if memory_context else "None"}

Return EXACT format:

REPLY:
<reply>

STORE: yes/no
CATEGORY: <one category>
"""

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ],
        temperature=0.3
    )

    raw = res.choices[0].message.content.strip()
    reply = raw
    store = False
    category = "general"

    if "STORE:" in raw:
        parts = raw.split("STORE:")
        reply = parts[0].replace("REPLY:", "").strip()
        store = "yes" in parts[1].lower()

        if "CATEGORY:" in parts[1]:
            category = parts[1].split("CATEGORY:")[1].strip()

    return reply, store, category

# ---------------- WEBHOOK ----------------
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")

    if not chat_id or not text:
        return {"ok": True}

    text = text.strip()

    # 1️⃣ Date / time fast-path
    quick = handle_date_time(text)
    if quick:
        await bot.send_message(chat_id=chat_id, text=quick)
        return {"ok": True}

    # 2️⃣ Memory context
    memories = search_memory(text)

    # 3️⃣ AI decision
    try:
        reply, should_store, category = ai_decide(text, memories)
    except Exception as e:
        print("AI error:", e)
        await bot.send_message(chat_id=chat_id, text="Something slipped. Try again.")
        return {"ok": True}

    # 4️⃣ Store memory if decided
    if should_store:
        try:
            emb = embed(text)
            supabase.table("memories").insert({
                "content": text,
                "category": category,
                "embedding": emb
            }).execute()
        except Exception as e:
            print("DB insert error:", e)

    # 5️⃣ Reply
    await bot.send_message(chat_id=chat_id, text=reply)
    return {"ok": True}
