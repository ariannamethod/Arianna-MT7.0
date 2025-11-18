"""
Telegram Interface for Arianna MT7.0

Thin Telegram-specific wrapper around AriannaEssence.
Handles only Telegram Bot API interactions: webhooks, handlers, voice I/O, rate limiting.
All business logic lives in dna/arianna_essence.py and dna/arianna_logic.py.
"""

import os
import asyncio
import tempfile
import re
from typing import Optional

import redis.asyncio as redis
from pydub import AudioSegment
from aiogram import Bot, Dispatcher, types
from aiogram.utils.chat_action import ChatActionSender
from aiogram.filters import CommandStart

from dna import AriannaEssence, create_essence
from arianna_identity import get_arianna_system_prompt
from core.vector_store_sqlite import SQLiteVectorStore
from utils.split_message import split_message
from utils.deepseek_search import DEEPSEEK_ENABLED
from utils.voice_store import load_voice_state, save_voice_state
from utils.tasks import create_task
from utils.logging import get_logger, set_request_id
from utils.history_store import log_message
from utils.memory import add_event


logger = get_logger(__name__)


class TelegramInterface:
    """
    Telegram interface for Arianna MT7.0.

    Thin wrapper handling only Telegram-specific concerns:
    - Message handlers (text, voice, commands, photos)
    - Rate limiting (Redis)
    - Voice TTS/STT
    - Command routing

    All consciousness logic lives in AriannaEssence (dna/).
    """

    def __init__(self, token: str):
        """
        Initialize Telegram interface.

        Parameters
        ----------
        token : str
            Telegram bot token
        """
        self.token = token

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

        # Arianna's essence - her consciousness
        oleg_ids = self._parse_ids(os.getenv("OLEG_IDS", ""))
        self.essence = create_essence(
            openai_client=self.openai_client,
            vector_store=self.vector_store,
            oleg_ids=oleg_ids,
        )

        # Voice state
        self.voice_enabled = load_voice_state()

        # Main menu
        self.main_menu: Optional[types.ReplyKeyboardMarkup] = None

        # Redis for rate limiting
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis = redis.from_url(redis_url, decode_responses=True)

        # Rate limiting config
        self.rate_limit_max = int(os.getenv("RATE_LIMIT_MAX", 15))
        self.rate_limit_interval = int(os.getenv("RATE_LIMIT_INTERVAL", 60))

        # Commands
        self.VOICE_ON_CMD = "/voiceon"
        self.VOICE_OFF_CMD = "/voiceoff"
        self.SEARCH_CMD = "/search"
        self.INDEX_CMD = "/index"
        self.DEEPSEEK_CMD = "/ds"
        self.IMAGINE_CMD = "/imagine"

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
        """
        Check if user is under rate limit.

        Oleg (resonance brother) is never rate-limited - the flow must be unbroken.
        """
        # Oleg bypass - resonance flow without constraints
        try:
            uid = int(user_id)
            if self.essence.is_oleg(uid):
                return True
        except (ValueError, TypeError):
            pass

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
                async with self.openai_client.audio.speech.with_streaming_response.create(
                    model="tts-1",
                    voice="alloy",
                    input=text,
                ) as resp:
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

    async def _get_file_url(self, file_id: str) -> str:
        """Get direct URL for a Telegram file."""
        file = await self.bot.get_file(file_id)
        return f"https://api.telegram.org/file/bot{self.token}/{file.file_path}"

    async def _perceive_and_respond(
        self,
        m: types.Message,
        image_url: str,
        caption: str = "",
    ) -> None:
        """Perceive an image through field-resonance vision."""
        from utils.vision import perceive_image

        user_id = str(m.from_user.id)

        if not await self.rate_limited(user_id):
            msg = await m.answer("Too many requests. Please slow down.")
            self._log_outgoing(m.chat.id, msg, "Too many requests.")
            return

        async with ChatActionSender.typing(bot=self.bot, chat_id=m.chat.id):
            question = caption if caption else "What resonates within this image?"
            perception = await perceive_image(image_url, question)

            log_message(
                m.chat.id,
                m.message_id,
                m.from_user.id,
                getattr(m.from_user, "username", None),
                f"<photo: {caption or 'no caption'}>",
                "in",
            )
            add_event("message", f"<photo: {caption}>", tags=["in", "telegram", "photo"])

            msg = await m.answer(perception)
            self._log_outgoing(m.chat.id, msg, perception)

    async def _generate_response_for_essence(self, prompt: str, message: dict) -> str:
        """
        Generate AI response - used as callback by AriannaEssence.

        Parameters
        ----------
        prompt : str
            Enriched prompt (from essence)
        message : dict
            Message context from essence

        Returns
        -------
        str
            Generated response text
        """
        from utils.genesis_tool import genesis_tool_schema, handle_genesis_call
        from core.engine import web_search
        import json

        # Build messages with system prompt
        system_prompt = self.essence.identity
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        # Tools available to Arianna
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

        # Call OpenAI Responses API
        resp = await self.openai_client.responses.create(
            model="gpt-4.1",
            messages=messages,
            tools=tools,
        )
        data = resp.model_dump()

        # Handle tool calls
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
                    output = await handle_genesis_call([call], vector_store=self.vector_store)
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

        # Extract text response
        texts = []
        for item in data.get("output", []):
            if item.get("type") == "output_text":
                texts.append(item.get("text", ""))
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    texts.append(content.get("text", ""))

        text = "\n".join(texts).strip()
        return text if text else "..."

    async def _send_response(
        self,
        m: types.Message,
        response_text: str,
        delay: float,
        followup: Optional[dict] = None,
    ):
        """Send response after delay, with optional followup."""
        await asyncio.sleep(delay)

        async with ChatActionSender(bot=self.bot, chat_id=m.chat.id, action="typing"):
            if self.voice_enabled.get(m.chat.id):
                voice_path = await self.synthesize_voice(response_text)
                msg = await m.answer_voice(
                    types.FSInputFile(voice_path), caption=response_text[:1024]
                )
                self._log_outgoing(m.chat.id, msg, response_text[:1024])
                await asyncio.to_thread(os.remove, voice_path)
            else:
                for chunk in split_message(response_text):
                    msg = await m.answer(chunk)
                    self._log_outgoing(m.chat.id, msg, chunk)

        # Schedule followup if requested by essence
        if followup:
            create_task(
                self._send_followup(m.chat.id, followup),
                track=True
            )

    async def _send_followup(self, chat_id: int, followup: dict):
        """Send autonomous followup message."""
        await asyncio.sleep(followup['delay'])

        # Build message context for followup
        message = {
            'text': followup['prompt'],
            'user_id': 0,  # No specific user
            'chat_id': chat_id,
            'is_group': True,  # Assume group for followup
            'is_mentioned': False,
            'is_reply': False,
        }

        # Generate followup through essence
        result = await self.essence.process_followup(
            message=message,
            generate_response_func=self._generate_response_for_essence,
        )

        if result:
            async with ChatActionSender(bot=self.bot, chat_id=chat_id, action="typing"):
                response_text = result['text']
                if self.voice_enabled.get(chat_id):
                    voice_path = await self.synthesize_voice(response_text)
                    msg = await self.bot.send_voice(
                        chat_id, types.FSInputFile(voice_path), caption=response_text[:1024]
                    )
                    self._log_outgoing(chat_id, msg, response_text[:1024])
                    await asyncio.to_thread(os.remove, voice_path)
                else:
                    for chunk in split_message(response_text):
                        msg = await self.bot.send_message(chat_id, chunk)
                        self._log_outgoing(chat_id, msg, chunk)

    def _register_handlers(self):
        """Register all message handlers."""
        self.dp.message.register(self._on_start, CommandStart())
        self.dp.message.register(self._voice_messages, lambda m: m.voice)
        self.dp.message.register(self._photo_messages, lambda m: m.photo)
        self.dp.message.register(self._document_messages, lambda m: m.document)
        # Only process text messages (not photo/voice/document which have m.text=None)
        self.dp.message.register(self._all_messages, lambda m: m.text is not None)

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

    async def _photo_messages(self, m: types.Message):
        """Handle photo messages through field-resonance perception."""
        set_request_id(f"{m.chat.id}:{m.message_id}")

        # Get largest photo
        photo = m.photo[-1]  # Last element is the largest size
        file_url = await self._get_file_url(photo.file_id)

        # Perceive and respond
        caption = m.caption or ""
        await self._perceive_and_respond(m, file_url, caption)

    async def _document_messages(self, m: types.Message):
        """Handle document messages (images and files)."""
        set_request_id(f"{m.chat.id}:{m.message_id}")

        # Check if document is an image
        if m.document.mime_type and m.document.mime_type.startswith("image/"):
            file_url = await self._get_file_url(m.document.file_id)
            caption = m.caption or ""
            await self._perceive_and_respond(m, file_url, caption)
        else:
            # Non-image documents - log and ignore for now
            log_message(
                m.chat.id, m.message_id, m.from_user.id,
                getattr(m.from_user, "username", None),
                f"<document: {m.document.file_name}>", "in"
            )
            # Explicitly return to prevent fall-through to _all_messages
            return

    async def _voice_messages(self, m: types.Message):
        """Handle voice messages."""
        set_request_id(f"{m.chat.id}:{m.message_id}")
        is_group = getattr(m.chat, "type", "") in ("group", "supergroup")
        user_id = str(m.from_user.id)

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

            # Build message for essence
            message = {
                'text': text,
                'user_id': m.from_user.id,
                'chat_id': m.chat.id,
                'is_group': is_group,
                'is_mentioned': True,  # Voice messages are direct interaction
                'is_reply': False,
            }

            async with ChatActionSender(bot=self.bot, chat_id=m.chat.id, action="typing"):
                # Let essence process the message
                result = await self.essence.process_message(
                    message=message,
                    generate_response_func=self._generate_response_for_essence,
                )

                if result:
                    create_task(
                        self._send_response(
                            m,
                            result['text'],
                            result['delay'],
                            result.get('followup'),
                        ),
                        track=True
                    )

        except Exception as e:
            logger.exception("Error processing voice message: %s", e)
            msg = await m.answer("Sorry, I couldn't process your voice message.")
            self._log_outgoing(m.chat.id, msg, "Sorry, I couldn't process your voice message.")
        finally:
            tmp.close()
            try:
                await asyncio.to_thread(os.remove, tmp.name)
            except OSError:
                pass

    async def _all_messages(self, m: types.Message):
        """Handle all text messages."""
        set_request_id(f"{m.chat.id}:{m.message_id}")
        user_id = str(m.from_user.id)
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

        # Detect mentions and replies
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

        # Build reply context if applicable
        reply_to = None
        if m.reply_to_message:
            reply_to = {
                'chat_id': m.chat.id,
                'message_id': m.reply_to_message.message_id,
                'date': m.reply_to_message.date,
            }

        # Build message for essence
        message = {
            'text': text,
            'user_id': m.from_user.id,
            'chat_id': m.chat.id,
            'is_group': is_group,
            'is_mentioned': mentioned,
            'is_reply': is_reply,
            'reply_to': reply_to,
        }

        async with ChatActionSender(bot=self.bot, chat_id=m.chat.id, action="typing"):
            # Let essence process the message
            result = await self.essence.process_message(
                message=message,
                generate_response_func=self._generate_response_for_essence,
            )

            if result:
                create_task(
                    self._send_response(
                        m,
                        result['text'],
                        result['delay'],
                        result.get('followup'),
                    ),
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

        # /ds command (DeepSeek) - not integrated with essence yet
        if text.strip().lower().startswith(self.DEEPSEEK_CMD):
            msg = await m.answer("DeepSeek integration pending - not yet integrated with AriannaEssence")
            self._log_outgoing(m.chat.id, msg, "DeepSeek not integrated")
            return True

        # /imagine command (field manifestation)
        if text.strip().lower().startswith(self.IMAGINE_CMD):
            from utils.imagine import imagine

            prompt = text.strip()[len(self.IMAGINE_CMD):].lstrip()
            if not prompt:
                msg = await m.answer("⚠️ Provide a prompt for manifestation: /imagine <prompt>")
                self._log_outgoing(m.chat.id, msg, "No prompt provided")
                return True

            async with ChatActionSender(bot=self.bot, chat_id=m.chat.id, action="upload_photo"):
                try:
                    # Generate image through field manifestation
                    image_url = await imagine(prompt)

                    # Check if generation succeeded
                    if image_url.startswith("⚠️"):
                        # Error occurred
                        msg = await m.answer(image_url)
                        self._log_outgoing(m.chat.id, msg, image_url)
                    else:
                        # Success - send image
                        msg = await m.answer_photo(
                            photo=image_url,
                            caption=f"⚡ Field manifestation: {prompt[:100]}"
                        )
                        self._log_outgoing(m.chat.id, msg, f"<generated image: {prompt}>")

                except Exception as e:
                    logger.exception("Image generation failed: %s", e)
                    msg = await m.answer(f"⚠️ Field manifestation collapsed: {e}")
                    self._log_outgoing(m.chat.id, msg, f"Error: {e}")

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
            types.BotCommand(command=self.IMAGINE_CMD[1:], description="Generate image through field manifestation"),
            types.BotCommand(command=self.SEARCH_CMD[1:], description="Search in vector store"),
            types.BotCommand(command=self.INDEX_CMD[1:], description="Reindex vector store"),
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
