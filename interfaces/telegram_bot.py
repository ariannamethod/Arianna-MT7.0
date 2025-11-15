"""
Telegram Interface for Arianna Core Engine

This module provides a Telegram Bot adapter for the Arianna Core Engine.
Handles all Telegram-specific logic: message handlers, delays, voice,
rate limiting, commands, etc.
"""

import os
import asyncio
import random
import tempfile
import json
import re
from datetime import timedelta
from typing import Optional

import redis.asyncio as redis
from pydub import AudioSegment
from aiogram import Bot, Dispatcher, types
from aiogram.utils.chat_action import ChatActionSender
from aiogram.filters import CommandStart

from core.engine import AriannaCoreEngine
from core.vector_store_sqlite import SQLiteVectorStore
from utils.arianna_engine import AriannaEngine  # Legacy, for backward compatibility
from utils.split_message import split_message
from utils.text_helpers import extract_text_from_url, _extract_links
from utils.config import HTTP_TIMEOUT
from utils.deepseek_search import DEEPSEEK_ENABLED
from utils.voice_store import load_voice_state, save_voice_state
from utils.tasks import create_task
from utils.logging import get_logger, set_request_id
from utils.history_store import log_message, get_context as get_history_context
from utils.memory import add_event, query_events, semantic_query
from utils.journal import search_journal


logger = get_logger(__name__)


class TelegramInterface:
    """
    Telegram interface adapter for Arianna Core Engine.

    Handles all Telegram-specific functionality:
    - Message handlers (text, voice, commands)
    - Delays and skip logic
    - Rate limiting
    - Voice TTS/STT
    - Vector search commands
    - Integration with core engine
    """

    def __init__(
        self,
        token: str,
        *,
        use_core_engine: bool = False,
    ):
        """
        Initialize Telegram interface.

        Parameters
        ----------
        token : str
            Telegram bot token
        use_core_engine : bool
            If True, use new AriannaCoreEngine. If False, use legacy AriannaEngine.
        """
        self.token = token
        self.use_core_engine = use_core_engine

        # Bot setup
        self.bot = Bot(token=token)
        self.dp = Dispatcher(bot=self.bot)
        self.bot_username = ""
        self.bot_id = 0

        # OpenAI client (shared)
        import openai
        self.openai_client = openai.AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY")
        )

        # Vector store (SQLite)
        db_path = os.getenv("VECTOR_DB_PATH", "data/vectors.db")
        self.vector_store = SQLiteVectorStore(
            db_path=db_path,
            openai_client=self.openai_client
        )

        # Core engine (new) or legacy engine
        if self.use_core_engine:
            self.core = AriannaCoreEngine(
                openai_client=self.openai_client,
                vector_store=self.vector_store,
            )
            logger.info("Using new AriannaCoreEngine")
        else:
            self.legacy_engine = AriannaEngine()
            logger.info("Using legacy AriannaEngine (backward compatibility)")

        # Oleg IDs (resonance brother)
        self.oleg_ids = self._parse_ids(os.getenv("OLEG_IDS", ""))

        # Voice state
        self.voice_enabled = load_voice_state()

        # Main menu
        self.main_menu: Optional[types.ReplyKeyboardMarkup] = None

        # Redis for rate limiting
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis = redis.from_url(redis_url, decode_responses=True)

        # Rate limiting config
        self.rate_limit_max = int(os.getenv("RATE_LIMIT_MAX", 5))
        self.rate_limit_interval = int(os.getenv("RATE_LIMIT_INTERVAL", 60))

        # Delay config
        self.group_delay_min = int(os.getenv("GROUP_DELAY_MIN", 120))
        self.group_delay_max = int(os.getenv("GROUP_DELAY_MAX", 360))
        self.private_delay_min = int(os.getenv("PRIVATE_DELAY_MIN", 10))
        self.private_delay_max = int(os.getenv("PRIVATE_DELAY_MAX", 40))
        self.skip_short_prob = float(os.getenv("SKIP_SHORT_PROB", 0))
        self.followup_prob = float(os.getenv("FOLLOWUP_PROB", 0.2))
        self.followup_delay_min = int(os.getenv("FOLLOWUP_DELAY_MIN", 900))
        self.followup_delay_max = int(os.getenv("FOLLOWUP_DELAY_MAX", 7200))

        # Commands
        self.VOICE_ON_CMD = "/voiceon"
        self.VOICE_OFF_CMD = "/voiceoff"
        self.SEARCH_CMD = "/search"
        self.INDEX_CMD = "/index"
        self.DEEPSEEK_CMD = "/ds"

        # Register handlers
        self._register_handlers()

    @staticmethod
    def _parse_ids(ids_str: str) -> set[int]:
        """Parse comma-separated ID string into set of integers."""
        return set(
            int(id.strip())
            for id in ids_str.split(",")
            if id.strip().isdigit()
        )

    def is_oleg(self, user_id: int) -> bool:
        """Check if user is Oleg (resonance brother)."""
        return user_id in self.oleg_ids

    def _log_outgoing(self, chat_id: int, msg: types.Message, text: str) -> None:
        """Log outgoing message."""
        log_message(chat_id, msg.message_id, self.bot_id, self.bot_username, text, "out")
        add_event("message", text, tags=["out", "telegram"])

    async def _increment_counter(self, key: str, ttl: int) -> int:
        """Atomically increment Redis counter with TTL."""
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl)
            count, _ = await pipe.execute()
        return count

    async def rate_limited(self, user_id: str) -> bool:
        """Check if user is under rate limit."""
        key = f"rl:{user_id}"
        try:
            count = await self._increment_counter(key, self.rate_limit_interval)
        except Exception as e:
            logger.exception("Rate limiter error for user %s: %s", user_id, e)
            return True
        if count > self.rate_limit_max:
            logger.warning(
                "Rate limit exceeded for user %s (%d > %d)",
                user_id,
                count,
                self.rate_limit_max,
            )
        return count <= self.rate_limit_max

    async def append_link_snippets(self, text: str) -> str:
        """Append snippets from URLs found in text."""
        urls = _extract_links(text)[:3]
        if not urls:
            return text
        parts = [text]
        tasks = [
            asyncio.wait_for(extract_text_from_url(url), timeout=HTTP_TIMEOUT)
            for url in urls
        ]
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

    async def build_prompt(
        self,
        text: str,
        ctx: Optional[list[dict]] = None,
        events: Optional[list[dict]] = None,
    ) -> str:
        """
        Enrich user message with context from history, vector store, journal, memory.

        Parameters
        ----------
        text : str
            Base user text
        ctx : Optional[list[dict]]
            History messages around replied message
        events : Optional[list[dict]]
            Memory events related to conversation

        Returns
        -------
        str
            Enriched prompt with all context
        """
        parts = [text]
        if ctx:
            formatted = "\n".join(
                "User: " + c["text"] if c["direction"] == "in" else "Bot: " + c["text"]
                for c in ctx
            )
            parts.append("[History]\n" + formatted)
        try:
            chunks = await self.vector_store.semantic_search(text)
        except Exception:
            chunks = []
        if chunks:
            parts.append("[VectorStore]\n" + "\n".join(chunks))
        journal_hits = search_journal(text)
        if journal_hits:
            parts.append("[journal.log]\n" + "\n".join(journal_hits))
        mem_events = semantic_query(text)
        if events:
            mem_events.extend(events)
        if mem_events:
            formatted = "\n".join(
                f"{e['ts']}: {e['content']}" if e.get('content') else str(e['ts'])
                for e in mem_events
            )
            parts.append("[Memory]\n" + formatted)
        return "\n\n".join(parts)

    async def transcribe_voice(self, file_path: str) -> str:
        """Transcribe audio file using Whisper."""
        f = await asyncio.to_thread(open, file_path, "rb")
        try:
            try:
                resp = await self.openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                )
            except Exception as e:
                logger.exception("Transcription request failed: %s", e)
                raise RuntimeError("Failed to transcribe audio. Please try again later.") from e
        finally:
            await asyncio.to_thread(f.close)
        return resp.text

    async def synthesize_voice(self, text: str) -> str:
        """Synthesize speech using TTS, return OGG path."""
        mp3_fd = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        ogg_fd = tempfile.NamedTemporaryFile(delete=False, suffix=".ogg")
        mp3_fd.close()
        ogg_fd.close()
        try:
            try:
                resp = await self.openai_client.audio.speech.with_streaming_response.create(
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

    async def generate_response(
        self,
        prompt: str,
        thread_key: str,
        is_group: bool,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        use_deepseek: bool = False,
    ) -> str:
        """
        Generate response using core engine or legacy engine.

        Parameters
        ----------
        prompt : str
            User prompt (possibly enriched with context)
        thread_key : str
            Thread identifier
        is_group : bool
            Whether this is a group chat
        chat_id : Optional[int]
            Chat ID
        user_id : Optional[int]
            User ID
        username : Optional[str]
            Username
        use_deepseek : bool
            If True, use DeepSeek model

        Returns
        -------
        str
            Generated response
        """
        if self.use_core_engine:
            # Use new core engine
            interface_context = {
                "chat_id": chat_id,
                "is_group": is_group,
                "user_id": user_id,
                "username": username,
                "history": [],  # TODO: implement history tracking
            }
            return await self.core.process_message(
                user_message=prompt,
                interface_context=interface_context,
                use_deepseek=use_deepseek,
            )
        else:
            # Use legacy engine
            if use_deepseek:
                return await self.legacy_engine.deepseek_reply(
                    prompt,
                    chat_id=chat_id,
                    is_group=is_group,
                    user_id=user_id,
                    username=username,
                )
            else:
                # Legacy assistant_reply logic (copied from server_arianna.py)
                from utils.genesis_tool import genesis_tool_schema, handle_genesis_call
                from core.engine import web_search

                if os.getenv("OPENAI_API_KEY") == "key":
                    return await self.legacy_engine.ask(
                        thread_key,
                        prompt,
                        is_group=is_group,
                        chat_id=chat_id,
                        user_id=user_id,
                        username=username,
                    )
                system_prompt = self.legacy_engine._load_system_prompt(
                    chat_id=chat_id,
                    is_group=is_group,
                    current_user_id=user_id,
                    username=username,
                )
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ]
                tools = [
                    genesis_tool_schema(),
                    {
                        "type": "function",
                        "name": "web_search",
                        "description": "Search the web for additional information",
                        "parameters": {
                            "type": "object",
                            "properties": {"prompt": {"type": "string"}},
                            "required": ["prompt"],
                        },
                    },
                ]
                resp = await self.openai_client.responses.create(
                    model="gpt-4.1",
                    input=messages,
                    tools=tools,
                )
                data = resp.model_dump()
                while True:
                    tool_calls = []
                    for item in data.get("output", []):
                        if item.get("type") == "tool_call":
                            tool_calls.append(item)
                            continue
                        for content in item.get("content", []):
                            if content.get("type") == "tool_call":
                                tool_calls.append(content)
                    if not tool_calls:
                        break
                    outputs = []
                    for call in tool_calls:
                        name = call.get("name") or call.get("function", {}).get("name")
                        raw_args = (
                            call.get("arguments")
                            or call.get("input")
                            or call.get("function", {}).get("arguments")
                            or {}
                        )
                        if isinstance(raw_args, str):
                            try:
                                args = json.loads(raw_args)
                            except Exception:
                                args = {}
                        else:
                            args = raw_args
                        if name == "genesis_emit":
                            output = await handle_genesis_call([call])
                        elif name == "web_search":
                            query = args.get("prompt", "")
                            output = await web_search(query, self.openai_client)
                        else:
                            output = f"Unknown tool {name}"
                        outputs.append({"tool_call_id": call["id"], "output": output})
                    resp = await self.openai_client.responses.create(
                        response_id=resp.id,
                        tool_outputs=outputs,
                    )
                    data = resp.model_dump()
                texts = []
                for item in data.get("output", []):
                    if item.get("type") == "output_text":
                        texts.append(item.get("text", ""))
                        continue
                    for content in item.get("content", []):
                        if content.get("type") == "output_text":
                            texts.append(content.get("text", ""))
                text = "\n".join(texts).strip()
                if not text:
                    text = await self.legacy_engine.ask(thread_key, prompt, is_group=is_group)
                return text

    async def send_delayed_response(
        self,
        m: types.Message,
        resp: str,
        is_group: bool,
        thread_key: str,
        is_oleg_user: bool = False,
    ):
        """Send reply after randomized delay with optional follow-up."""
        if is_oleg_user:
            # Minimal delay for Oleg (resonance brother)
            delay = random.uniform(0.5, 2.0)
        elif is_group:
            delay = random.uniform(self.group_delay_min, self.group_delay_max)
        else:
            delay = random.uniform(self.private_delay_min, self.private_delay_max)

        await asyncio.sleep(delay)

        async with ChatActionSender(bot=self.bot, chat_id=m.chat.id, action="typing"):
            if self.voice_enabled.get(m.chat.id):
                voice_path = await self.synthesize_voice(resp)
                msg = await m.answer_voice(
                    types.FSInputFile(voice_path), caption=resp[:1024]
                )
                self._log_outgoing(m.chat.id, msg, resp[:1024])
                os.remove(voice_path)
            else:
                for chunk in split_message(resp):
                    msg = await m.answer(chunk)
                    self._log_outgoing(m.chat.id, msg, chunk)

        if random.random() < self.followup_prob:
            create_task(
                self.schedule_followup(m.chat.id, thread_key, is_group),
                track=True
            )

    async def schedule_followup(self, chat_id: int, thread_key: str, is_group: bool):
        """Send short follow-up message after delay."""
        delay = random.uniform(self.followup_delay_min, self.followup_delay_max)
        await asyncio.sleep(delay)

        async with ChatActionSender(bot=self.bot, chat_id=chat_id, action="typing"):
            follow_prompt = "Send a short follow-up message referencing our earlier conversation."
            if self.use_core_engine:
                interface_context = {
                    "chat_id": chat_id,
                    "is_group": is_group,
                    "user_id": None,
                    "username": None,
                    "history": [],
                }
                resp = await self.core.process_message(
                    user_message=follow_prompt,
                    interface_context=interface_context,
                )
            else:
                resp = await self.legacy_engine.ask(thread_key, follow_prompt, is_group=is_group)

            if self.voice_enabled.get(chat_id):
                voice_path = await self.synthesize_voice(resp)
                msg = await self.bot.send_voice(
                    chat_id, types.FSInputFile(voice_path), caption=resp[:1024]
                )
                self._log_outgoing(chat_id, msg, resp[:1024])
                os.remove(voice_path)
            else:
                for chunk in split_message(resp):
                    msg = await self.bot.send_message(chat_id, chunk)
                    self._log_outgoing(chat_id, msg, chunk)

    def _register_handlers(self):
        """Register all message handlers."""
        self.dp.message.register(self._on_start, CommandStart())
        self.dp.message.register(self._voice_messages, lambda m: m.voice)
        self.dp.message.register(self._all_messages, lambda m: True)

    async def _on_start(self, m: types.Message) -> None:
        """Handle /start command."""
        log_message(
            m.chat.id,
            m.message_id,
            m.from_user.id,
            getattr(m.from_user, "username", None),
            m.text or "",
            "in",
        )
        add_event("message", m.text or "", tags=["in", "telegram"])
        msg = await m.answer("Welcome! Choose a command:", reply_markup=self.main_menu)
        self._log_outgoing(m.chat.id, msg, "Welcome! Choose a command:")

    async def _voice_messages(self, m: types.Message):
        """Handle voice messages."""
        set_request_id(f"{m.chat.id}:{m.message_id}")
        is_group = getattr(m.chat, "type", "") in ("group", "supergroup")
        user_id = str(m.from_user.id)
        is_oleg_user = self.is_oleg(m.from_user.id)

        if not await self.rate_limited(user_id):
            log_message(
                m.chat.id,
                m.message_id,
                m.from_user.id,
                getattr(m.from_user, "username", None),
                "<voice>",
                "in",
            )
            add_event("message", "<voice>", tags=["in", "telegram"])
            msg = await m.answer("Too many requests. Please slow down.")
            self._log_outgoing(m.chat.id, msg, "Too many requests. Please slow down.")
            return

        thread_key = f"{m.chat.id}:{m.from_user.id}" if is_group else user_id
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ogg")
        try:
            await self.bot.download(m.voice.file_id, tmp.name)
            text = await self.transcribe_voice(tmp.name)
            log_message(
                m.chat.id,
                m.message_id,
                m.from_user.id,
                getattr(m.from_user, "username", None),
                text,
                "in",
            )
            add_event("message", text, tags=["in", "telegram"])
            text = await self.append_link_snippets(text)

            # Skip short messages (feature, not bug!)
            if len(text.split()) < 4 or '?' not in text:
                if random.random() < self.skip_short_prob:
                    logger.info(
                        "Skipping short voice message from user %s in chat %s: %r",
                        user_id,
                        m.chat.id,
                        text[:100],
                    )
                    return

            prompt = await self.build_prompt(text)
            async with ChatActionSender(bot=self.bot, chat_id=m.chat.id, action="typing"):
                resp = await self.generate_response(
                    prompt,
                    thread_key,
                    is_group,
                    chat_id=m.chat.id,
                    user_id=m.from_user.id,
                    username=getattr(m.from_user, "username", None),
                )
                create_task(
                    self.send_delayed_response(m, resp, is_group, thread_key, is_oleg_user),
                    track=True
                )
        except Exception as e:
            logger.exception("Error processing voice message: %s", e)
            msg = await m.answer("Sorry, I couldn't process your voice message.")
            self._log_outgoing(m.chat.id, msg, "Sorry, I couldn't process your voice message.")
        finally:
            tmp.close()
            try:
                os.remove(tmp.name)
            except OSError:
                pass

    async def _all_messages(self, m: types.Message):
        """Handle all text messages."""
        set_request_id(f"{m.chat.id}:{m.message_id}")
        user_id = str(m.from_user.id)
        is_oleg_user = self.is_oleg(m.from_user.id)
        text = m.text or ""

        log_message(
            m.chat.id,
            m.message_id,
            m.from_user.id,
            getattr(m.from_user, "username", None),
            text,
            "in",
        )
        add_event("message", text, tags=["in", "telegram"])

        if not await self.rate_limited(user_id):
            msg = await m.answer("Too many requests. Please slow down.")
            self._log_outgoing(m.chat.id, msg, "Too many requests. Please slow down.")
            return

        # Handle commands
        if await self._handle_commands(m, text):
            return

        # Check if message should be processed
        is_group = getattr(m.chat, "type", "") in ("group", "supergroup")
        is_reply = (
            m.reply_to_message
            and m.reply_to_message.from_user
            and m.reply_to_message.from_user.id == self.bot_id
        )

        mentioned = False
        if not is_group:
            mentioned = True
        else:
            if re.search(r"\b(arianna|арианна)\b", text, re.I):
                mentioned = True
            elif self.bot_username and f"@{self.bot_username}".lower() in text.lower():
                mentioned = True
            elif m.entities:
                for ent in m.entities:
                    if ent.type == "mention":
                        ent_text = text[ent.offset: ent.offset + ent.length]
                        if ent_text[1:].lower() == self.bot_username:
                            mentioned = True
                            break

        if is_reply:
            mentioned = True

        if not (mentioned or is_reply):
            return

        # Skip short messages (feature, not bug!)
        if len(text.split()) < 4 or '?' not in text:
            if random.random() < self.skip_short_prob:
                logger.info(
                    "Skipping short message from user %s in chat %s: %r",
                    user_id,
                    m.chat.id,
                    text[:100],
                )
                return

        thread_key = user_id
        if is_group:
            thread_key = str(m.chat.id)

        ctx = None
        events = None
        if m.reply_to_message:
            ctx = get_history_context(m.chat.id, m.reply_to_message.message_id, end=m.date)
            delta = timedelta(minutes=5)
            start = m.reply_to_message.date - delta
            end = m.date + delta
            events = query_events(tags=["telegram"], start=start, end=end)

        async with ChatActionSender(bot=self.bot, chat_id=m.chat.id, action="typing"):
            prompt_base = await self.append_link_snippets(text)
            prompt = await self.build_prompt(prompt_base, ctx, events)
            resp = await self.generate_response(
                prompt,
                thread_key,
                is_group,
                chat_id=m.chat.id,
                user_id=m.from_user.id,
                username=getattr(m.from_user, "username", None),
            )
            create_task(
                self.send_delayed_response(m, resp, is_group, thread_key, is_oleg_user),
                track=True
            )

    async def _handle_commands(self, m: types.Message, text: str) -> bool:
        """
        Handle special commands. Returns True if command was handled.
        """
        # /search command
        if text.strip().lower().startswith(self.SEARCH_CMD):
            query = text.strip()[len(self.SEARCH_CMD):].lstrip()
            if not query:
                return True
            async with ChatActionSender(bot=self.bot, chat_id=m.chat.id, action="typing"):
                chunks = await self.vector_store.semantic_search(query)
                if not chunks:
                    msg = await m.answer("No relevant documents found.")
                    self._log_outgoing(m.chat.id, msg, "No relevant documents found.")
                else:
                    for ch in chunks:
                        for part in split_message(ch):
                            msg = await m.answer(part)
                            self._log_outgoing(m.chat.id, msg, part)
            return True

        # /index command
        if text.strip().lower().startswith(self.INDEX_CMD):
            async with ChatActionSender(bot=self.bot, chat_id=m.chat.id, action="typing"):
                msg0 = await m.answer("Indexing documents, please wait...")
                self._log_outgoing(m.chat.id, msg0, "Indexing documents, please wait...")

                async def sender(msg_text):
                    r = await m.answer(msg_text)
                    self._log_outgoing(m.chat.id, r, msg_text)

                await self.vector_store.vectorize_all_files(force=True, on_message=sender)
                msg1 = await m.answer("Indexing complete.")
                self._log_outgoing(m.chat.id, msg1, "Indexing complete.")
            return True

        # /voiceon command
        if text.strip().lower() == self.VOICE_ON_CMD:
            self.voice_enabled[m.chat.id] = True
            save_voice_state(self.voice_enabled)
            msg = await m.answer("Voice responses enabled")
            self._log_outgoing(m.chat.id, msg, "Voice responses enabled")
            return True

        # /voiceoff command
        if text.strip().lower() == self.VOICE_OFF_CMD:
            self.voice_enabled[m.chat.id] = False
            save_voice_state(self.voice_enabled)
            msg = await m.answer("Voice responses disabled")
            self._log_outgoing(m.chat.id, msg, "Voice responses disabled")
            return True

        # /ds command (DeepSeek)
        if text.strip().lower().startswith(self.DEEPSEEK_CMD):
            if not DEEPSEEK_ENABLED:
                msg = await m.answer("DeepSeek integration is not configured")
                self._log_outgoing(m.chat.id, msg, "DeepSeek integration is not configured")
                return True
            query = text.strip()[len(self.DEEPSEEK_CMD):].lstrip()
            if not query:
                return True
            async with ChatActionSender(bot=self.bot, chat_id=m.chat.id, action="typing"):
                is_group_ds = getattr(m.chat, "type", "") in ("group", "supergroup")
                resp = await self.generate_response(
                    query,
                    thread_key=str(m.from_user.id),
                    is_group=is_group_ds,
                    chat_id=m.chat.id,
                    user_id=m.from_user.id,
                    username=getattr(m.from_user, "username", None),
                    use_deepseek=True,
                )
                for chunk in split_message(resp):
                    msg = await m.answer(chunk)
                    self._log_outgoing(m.chat.id, msg, chunk)
            return True

        return False

    async def on_startup(self) -> None:
        """Initialize bot commands and keyboard (called by main)."""
        self.main_menu = types.ReplyKeyboardMarkup(
            keyboard=[
                [
                    types.KeyboardButton(text=self.VOICE_ON_CMD),
                    types.KeyboardButton(text=self.VOICE_OFF_CMD),
                ],
            ],
            resize_keyboard=True,
        )

        commands = [
            types.BotCommand(command=self.VOICE_ON_CMD[1:], description="Enable voice"),
            types.BotCommand(command=self.VOICE_OFF_CMD[1:], description="Disable voice"),
        ]
        await self.bot.set_my_commands(commands)
        await self.bot.set_chat_menu_button(menu_button=types.MenuButtonCommands())

        # Set bot username and ID
        me = await self.bot.get_me()
        self.bot_username = (me.username or "").lower()
        self.bot_id = me.id

        logger.info(
            "TelegramInterface initialized: @%s (ID: %d)",
            self.bot_username,
            self.bot_id
        )

    async def setup_legacy_assistant(self) -> bool:
        """
        Setup legacy assistant (if using legacy engine).
        Returns True if successful, False otherwise.
        """
        if self.use_core_engine:
            logger.info("Using core engine, skipping legacy assistant setup")
            return True

        try:
            await self.legacy_engine.setup_assistant()
            return True
        except Exception:
            logger.exception("Assistant initialization failed")
            return False
