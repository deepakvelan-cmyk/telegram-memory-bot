import os
from datetime import datetime
from fastapi import FastAPI, Request
from telegram import Bot
from supabase import create_client
from openai import OpenAI

# ---------- ENV ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ---------- CLIENTS ----------
bot = Bot(token=TELEGRAM_TOKEN)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# ---------- EMBEDDINGS ----------
def embed(text: str):
    res = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return res.data[0].embedding

# ---------- MEMORY SEARCH ----------
def search_memory(text: str):
    try:
        emb = embed(text)
        result = supabase.rpc(
            "match_memories",
            {
                "query_embedding": emb,
                "match_threshold": 0.65,
                "match_count": 6
            }
        ).execute()
        return result.data or []
    except Exception as e:
        print("Search error:", e)
        return []

# ---------- DATE / TIME (HARD RULE, NO AI) ----------
def handle_date_time(text: str):
    t = text.lower()

    if "date" in t:
        return datetime.now().strftime("Today's date is %B %d, %Y.")

    if "time" in t or "now" in t:
        return datetime.now().strftime("The current time is %I:%M %p.")

    return None

# ---------- AI BRAIN ----------
def ai_think(user_text: str, memories: list):
    memory_context = "\n".join(
        f"- {m['content']} (category: {m.get('category','general')})"
        for m in memories
    )

    system_prompt = f"""
You are a personal AI assistant for ONE user.

Rules:
- You can answer questions without storing
- Decide if something should be remembered
- Choose a category when storing
- NEVER hallucinate dates or time
- Be concise and human

If storing, follow this exact format:

REPLY:
<message>

STORE: yes/no
CATEGORY: <category>

Past memories:
{memory_context if memory_context else "None"}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ],
        temperature=0.4
    )

    content = response.choices[0].message.content.strip()

    reply = content
    store = False
    category = "general"

    if "STORE:" in content:
        reply = content.split("STORE:")[0].replace("REPLY:", "").strip()
        store_line = content.split("STORE:")[1].lower()
        store = store_line.startswith("yes")

        if "category:" in store_line:
            category = store_line.split("category:")[1].strip()

    return reply, store, category

# ---------- WEBHOOK ----------
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()

    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")

    if not chat_id or not text:
        return {"ok": True}

    text = text.strip()

    # 1️⃣ Date / Time (no AI)
    date_reply = handle_date_time(text)
    if date_reply:
        await bot.send_message(chat_id=chat_id, text=date_reply)
        return {"ok": True}

    # 2️⃣ Memory context
    memories = search_memory(text)

    # 3️⃣ AI reasoning
    try:
        reply, should_store, category = ai_think(text, memories)
    except Exception as e:
        print("AI error:", e)
        await bot.send_message(chat_id=chat_id, text="Something went wrong while thinking.")
        return {"ok": True}

    # 4️⃣ Store memory if needed
    if should_store:
        try:
            emb = embed(text)
            supabase.table("memories").insert({
                "content": text,
                "category": category,
                "embedding": emb
            }).execute()
        except Exception as e:
            print("Insert error:", e)

    # 5️⃣ Reply
    await bot.send_message(chat_id=chat_id, text=reply)
    return {"ok": True}
