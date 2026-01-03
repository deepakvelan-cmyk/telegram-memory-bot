import os
from fastapi import FastAPI, Request
from telegram import Bot
from supabase import create_client
from openai import OpenAI

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ---------- CLIENTS ----------
bot = Bot(token=TELEGRAM_TOKEN)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# ---------- HELPERS ----------
def embed(text: str):
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding


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


# ---------- TELEGRAM WEBHOOK ----------
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()

    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")

    if not chat_id or not text:
        return {"ok": True}

    text = text.strip()

    # If it's a question → search memory
    if "?" in text.lower():
        try:
            query_emb = embed(text)
            memories = search_memory(query_emb)

            if not memories:
                reply = "I don’t have anything relevant yet."
            else:
                items = [f"- {m['content']}" for m in memories]
                reply = "Here’s what you have pending:\n" + "\n".join(items)

        except Exception as e:
            print("Search error:", e)
            reply = "I’m set up, but my AI quota is exhausted right now."

    # Otherwise → store memory (but ignore noise)
    else:
        if len(text) < 10:
            reply = "Hey 🙂 How can I help?"
        else:
            try:
                emb = embed(text)
                category = categorize(text)

                supabase.table("memories").insert({
                    "content": text,
                    "category": category,
                    "embedding": emb
                }).execute()

                reply = "Got it — I’ll remember that."
            except Exception as e:
                print("Insert error:", e)
                reply = "I couldn’t save that right now, but I’m still here."

    await bot.send_message(chat_id=chat_id, text=reply)
    return {"ok": True}
