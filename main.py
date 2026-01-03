import os
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
    except Exception:
        return []


# ---------- AI BRAIN ----------
def ai_think(user_text: str, memories: list):
    memory_context = "\n".join(
        f"- {m['content']} (category: {m['category']})"
        for m in memories
    )

    system_prompt = f"""
You are a personal AI assistant for ONE user.

You are their only assistant.
You remember things over time and evolve as memories grow.

You must:
- Answer questions naturally (date, time, advice, reminders)
- Use memories when helpful, but do NOT depend on them
- Decide if something is worth remembering
- Decide a category if storing (personal, work, reminder, high_priority, link, general)
- Be calm, concise, and human

If something is just a question, do NOT store it.
If something is an event, reminder, decision, or task, store it.

Relevant past memories (may be empty):
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

    """
    Expected AI format (strict):

    REPLY:
    <what to say to user>

    STORE: yes/no
    CATEGORY: <category>
    """

    reply = content
    store = False
    category = "general"

    if "STORE:" in content:
        parts = content.split("STORE:")
        reply = parts[0].replace("REPLY:", "").strip()

        store_line = parts[1].strip().lower()
        store = store_line.startswith("yes")

        if "CATEGORY:" in store_line:
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

    # 1️⃣ Fetch memories (context only)
    memories = search_memory(text)

    # 2️⃣ AI thinks
    try:
        reply, should_store, category = ai_think(text, memories)
    except Exception as e:
        print("AI error:", e)
        await bot.send_message(chat_id=chat_id, text="I’m thinking, but something went wrong.")
        return {"ok": True}

    # 3️⃣ Store if AI decides
    if should_store:
        try:
            emb = embed(text)
            supabase.table("memories").insert({
                "content": text,
                "category": category,
                "embedding": emb
            }).execute()
        except Exception as e:
            print("Memory insert error:", e)

    # 4️⃣ Reply
    await bot.send_message(chat_id=chat_id, text=reply)
    return {"ok": True}
