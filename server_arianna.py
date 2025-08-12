import os
import re
import asyncio
import random
import logging
import tempfile
import time

import redis.asyncio as redis

import openai
from pydub import AudioSegment
from aiogram import Bot, Dispatcher, types
from aiogram.utils.chat_action import ChatActionSender
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from utils.arianna_engine import AriannaEngine
from utils.split_message import split_message
from utils.vector_store import VectorStore
from utils.text_helpers import extract_text_from_url
from utils.deepseek_search import DEEPSEEK_ENABLED
from utils.voice_store import load_voice_state, save_voice_state
from utils.tasks import create_task
from utils.genesis_service import start_genesis_service

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

BOT_TOKEN     = os.getenv("TELEGRAM_TOKEN")
BOT_USERNAME  = ""  # will be set at startup
BOT_ID        = 0   # will be set at startup

bot    = Bot(token=BOT_TOKEN)
dp     = Dispatcher(bot=bot)
engine = AriannaEngine()
openai_client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
vector_store = VectorStore(openai_client=openai_client)
DEEPSEEK_CMD = "/ds"
SEARCH_CMD = "/search"
INDEX_CMD = "/index"
VOICE_ON_CMD = "/voiceon"
VOICE_OFF_CMD = "/voiceoff"
VOICE_ENABLED = load_voice_state()

# --- Redis-backed per-user rate limiting ---
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", 5))
RATE_LIMIT_INTERVAL = int(os.getenv("RATE_LIMIT_INTERVAL", 60))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis = redis.from_url(REDIS_URL, decode_responses=True)


async def rate_limited(user_id: str) -> bool:
    """Return True if under limit; otherwise False."""
    key = f"rl:{user_id}"
    now = time.time()
    window_start = now - RATE_LIMIT_INTERVAL
    try:
        async with _redis.pipeline() as pipe:
            pipe.zadd(key, {str(now): now})
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.expire(key, RATE_LIMIT_INTERVAL)
            _, _, count, _ = await pipe.execute()
    except Exception as e:
        logger.exception("Rate limiter error for user %s: %s", user_id, e)
        return True
    if count > RATE_LIMIT_MAX:
        logger.warning(
            "Rate limit exceeded for user %s (%d > %d)",
            user_id,
            count,
            RATE_LIMIT_MAX,
        )
    return count <= RATE_LIMIT_MAX

# --- optional behavior tuning ---
GROUP_DELAY_MIN   = int(os.getenv("GROUP_DELAY_MIN", 120))   # 2 minutes
GROUP_DELAY_MAX   = int(os.getenv("GROUP_DELAY_MAX", 360))   # 6 minutes
PRIVATE_DELAY_MIN = int(os.getenv("PRIVATE_DELAY_MIN", 10))  # 10 seconds
PRIVATE_DELAY_MAX = int(os.getenv("PRIVATE_DELAY_MAX", 40))  # 40 seconds
SKIP_SHORT_PROB   = float(os.getenv("SKIP_SHORT_PROB", 0.75))
FOLLOWUP_PROB     = float(os.getenv("FOLLOWUP_PROB", 0.2))
FOLLOWUP_DELAY_MIN = int(os.getenv("FOLLOWUP_DELAY_MIN", 900))   # 15 minutes
FOLLOWUP_DELAY_MAX = int(os.getenv("FOLLOWUP_DELAY_MAX", 7200))  # 2 hours

# Regex for detecting links
URL_REGEX = re.compile(r"https://\S+")


async def append_link_snippets(text: str) -> str:
    """Append snippet from any https:// link in the text."""
    urls = URL_REGEX.findall(text)[:3]
    if not urls:
        return text
    parts = [text]
    tasks = [asyncio.wait_for(extract_text_from_url(url), timeout=10) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for url, result in zip(urls, results):
        if isinstance(result, asyncio.TimeoutError):
            logger.warning("Timeout fetching %s", url)
            snippet = "[Timeout]"
        elif isinstance(result, Exception):
            logger.exception("Error loading page %s: %s", url, result)
            snippet = f"[Error loading page: {result}]"
        else:
            snippet = result
        parts.append(f"\n[Snippet from {url}]\n{snippet[:500]}")
    return "\n".join(parts)


async def transcribe_voice(file_path: str) -> str:
    """Transcribe an audio file using OpenAI Whisper."""
    f = await asyncio.to_thread(open, file_path, "rb")
    try:
        try:
            resp = await openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
            )
        except Exception as e:
            logger.exception("Transcription request failed: %s", e)
            raise RuntimeError("Failed to transcribe audio. Please try again later.") from e
    finally:
        await asyncio.to_thread(f.close)
    return resp.text


async def synthesize_voice(text: str) -> str:
    """Synthesize speech from text using OpenAI TTS and return OGG path."""
    mp3_fd = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    ogg_fd = tempfile.NamedTemporaryFile(delete=False, suffix=".ogg")
    mp3_fd.close()
    ogg_fd.close()
    try:
        try:
            resp = await openai_client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice="alloy",
                input=text,
            )
            await resp.stream_to_file(mp3_fd.name)
        except Exception as e:
            logger.exception("Speech synthesis request failed: %s", e)
            raise RuntimeError("Failed to synthesize voice. Please try again later.") from e
        await asyncio.to_thread(
            lambda: AudioSegment.from_file(mp3_fd.name).export(
                ogg_fd.name, format="ogg", codec="libopus"
            )
        )
    finally:
        await asyncio.to_thread(os.remove, mp3_fd.name)
    return ogg_fd.name


async def send_delayed_response(m: types.Message, resp: str, is_group: bool, thread_key: str):
    """Send the reply after a randomized delay and schedule optional follow-up."""
    if is_group:
        delay = random.uniform(GROUP_DELAY_MIN, GROUP_DELAY_MAX)
    else:
        delay = random.uniform(PRIVATE_DELAY_MIN, PRIVATE_DELAY_MAX)
    await asyncio.sleep(delay)
    async with ChatActionSender(bot=bot, chat_id=m.chat.id, action="typing"):
        if VOICE_ENABLED.get(m.chat.id):
            voice_path = await synthesize_voice(resp)
            await m.answer_voice(types.FSInputFile(voice_path), caption=resp[:1024])
            os.remove(voice_path)
        else:
            for chunk in split_message(resp):
                await m.answer(chunk)

    if random.random() < FOLLOWUP_PROB:
        create_task(schedule_followup(m.chat.id, thread_key, is_group), track=True)


async def schedule_followup(chat_id: int, thread_key: str, is_group: bool):
    """Send a short follow-up message referencing the earlier conversation."""
    delay = random.uniform(FOLLOWUP_DELAY_MIN, FOLLOWUP_DELAY_MAX)
    await asyncio.sleep(delay)
    async with ChatActionSender(bot=bot, chat_id=chat_id, action="typing"):
        follow_prompt = "Send a short follow-up message referencing our earlier conversation."
        resp = await engine.ask(thread_key, follow_prompt, is_group=is_group)
        if VOICE_ENABLED.get(chat_id):
            voice_path = await synthesize_voice(resp)
            await bot.send_voice(chat_id, types.FSInputFile(voice_path), caption=resp[:1024])
            os.remove(voice_path)
        else:
            for chunk in split_message(resp):
                await bot.send_message(chat_id, chunk)

# --- health check routes ---
async def healthz(request):
    return web.Response(text="ok")


async def status(request):
    return web.Response(text="running")


@dp.message(lambda m: m.voice)
async def voice_messages(m: types.Message):
    is_group = getattr(m.chat, "type", "") in ("group", "supergroup")
    user_id = str(m.from_user.id)
    if not await rate_limited(user_id):
        await m.answer("Too many requests. Please slow down.")
        return
    thread_key = f"{m.chat.id}:{m.from_user.id}" if is_group else user_id
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
        await bot.download(m.voice.file_id, tmp.name)
    text = await transcribe_voice(tmp.name)
    os.remove(tmp.name)
    text = await append_link_snippets(text)
    if len(text.split()) < 4 or '?' not in text:
        if random.random() < SKIP_SHORT_PROB:
            return
    async with ChatActionSender(bot=bot, chat_id=m.chat.id, action="typing"):
        resp = await engine.ask(thread_key, text, is_group=is_group)
        create_task(send_delayed_response(m, resp, is_group, thread_key), track=True)

@dp.message(lambda m: True)
async def all_messages(m: types.Message):
    user_id = str(m.from_user.id)
    if not await rate_limited(user_id):
        await m.answer("Too many requests. Please slow down.")
        return
    text    = m.text or ""

    # Semantic search
    if text.strip().lower().startswith(SEARCH_CMD):
        query = text.strip()[len(SEARCH_CMD):].lstrip()
        if not query:
            return
        async with ChatActionSender(bot=bot, chat_id=m.chat.id, action="typing"):
            chunks = await vector_store.semantic_search(query)
            if not chunks:
                await m.answer("No relevant documents found.")
            else:
                for ch in chunks:
                    for part in split_message(ch):
                        await m.answer(part)
        return

    if text.strip().lower().startswith(INDEX_CMD):
        async with ChatActionSender(bot=bot, chat_id=m.chat.id, action="typing"):
            await m.answer("Indexing documents, please wait...")

            async def sender(msg):
                await m.answer(msg)

            await vector_store.vectorize_all_files(force=True, on_message=sender)
            await m.answer("Indexing complete.")
        return

    if text.strip().lower() == VOICE_ON_CMD:
        VOICE_ENABLED[m.chat.id] = True
        save_voice_state(VOICE_ENABLED)
        await m.answer("Voice responses enabled")
        return
    if text.strip().lower() == VOICE_OFF_CMD:
        VOICE_ENABLED[m.chat.id] = False
        save_voice_state(VOICE_ENABLED)
        await m.answer("Voice responses disabled")
        return

    # Direct DeepSeek call
    if text.strip().lower().startswith(DEEPSEEK_CMD):
        if not DEEPSEEK_ENABLED:
            await m.answer("DeepSeek integration is not configured")
            return
        query = text.strip()[len(DEEPSEEK_CMD):].lstrip()
        if not query:
            return
        async with ChatActionSender(bot=bot, chat_id=m.chat.id, action="typing"):
            resp = await engine.deepseek_reply(query)
            for chunk in split_message(resp):
                await m.answer(chunk)
        return

    # Простая проверка упоминания бота в группах
    is_group = getattr(m.chat, "type", "") in ("group", "supergroup")
    is_reply = (
        m.reply_to_message
        and m.reply_to_message.from_user
        and m.reply_to_message.from_user.id == BOT_ID
    )

    mentioned = False
    if not is_group:
        mentioned = True
    else:
        if re.search(r"\b(arianna|арианна)\b", text, re.I):
            mentioned = True
        elif BOT_USERNAME and f"@{BOT_USERNAME}".lower() in text.lower():
            mentioned = True
        elif m.entities:
            for ent in m.entities:
                if ent.type == "mention":
                    ent_text = text[ent.offset: ent.offset + ent.length]
                    if ent_text[1:].lower() == BOT_USERNAME:
                        mentioned = True
                        break

    if is_reply:
        mentioned = True

    if not (mentioned or is_reply):
        return

    if len(text.split()) < 4 or '?' not in text:
        if random.random() < SKIP_SHORT_PROB:
            return

    thread_key = user_id
    if is_group:
        thread_key = str(m.chat.id)  # shared history for the whole group

    async with ChatActionSender(bot=bot, chat_id=m.chat.id, action="typing"):
        # Генерируем ответ через Assistants API
        prompt = await append_link_snippets(text)
        resp = await engine.ask(thread_key, prompt, is_group=is_group)
        create_task(send_delayed_response(m, resp, is_group, thread_key), track=True)

async def main():
    global BOT_USERNAME, BOT_ID
    # получаем имя бота и создаём ассистента и любые ресурс‑ассеты
    me = await bot.get_me()
    BOT_USERNAME = (me.username or "").lower()
    BOT_ID = me.id

    init_failed = False
    try:
        await engine.setup_assistant()
    except Exception:
        logger.exception("Assistant initialization failed")
        init_failed = True

    start_genesis_service()
    app = web.Application()
    path = f"/webhook/{BOT_TOKEN}"
    if init_failed:
        async def failed(request):
            return web.Response(status=500, text="Initialization failed")
        app.router.add_route("*", path, failed)
        app.router.add_route("*", "/webhook", failed)
    else:
        handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        handler.register(app, path=path)
        handler.register(app, path="/webhook")
        setup_application(app, dp)

    # Register health check routes
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/status", status)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🚀 Arianna webhook started on port {port}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())

