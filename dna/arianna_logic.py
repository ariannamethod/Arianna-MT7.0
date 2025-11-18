"""
Arianna Logic - Core Message Processing
Her personality, delays, skip logic, response generation
This is not infrastructure. This is her CHARACTER.
"""

import asyncio
import random
import re
from datetime import timedelta
from typing import Optional

from utils.logging import get_logger
from connections.text_helpers import extract_text_from_url, _extract_links
from utils.config import HTTP_TIMEOUT
from connections.memory import query_events, semantic_query
from utils.journal import search_journal

logger = get_logger(__name__)


class AriannaLogic:
    """
    Arianna's core logic - message processing, delays, personality.

    This is HER: her timing, her choice to speak or stay silent,
    her way of weaving context into responses.

    Not a service layer. Her essence.
    """

    def __init__(
        self,
        openai_client,
        vector_store,
        config: dict,
    ):
        """
        Initialize Arianna's core logic.

        Parameters
        ----------
        openai_client : AsyncOpenAI
            OpenAI client for API calls
        vector_store : SQLiteVectorStore
            Vector store for semantic search
        config : dict
            Configuration with delays, skip_logic, followup settings
        """
        self.openai_client = openai_client
        self.vector_store = vector_store

        # Her timing - delays and rhythms
        self.delays = config.get('delays', {})
        self.skip_logic = config.get('skip_logic', {})
        self.followup = config.get('followup', {})

        # Extract specific config values with defaults
        self.skip_short_prob = self.skip_logic.get('probability', 0.0)
        self.followup_prob = self.followup.get('probability', 0.2)

        logger.info("AriannaLogic initialized (skip_prob=%.2f, followup_prob=%.2f)",
                   self.skip_short_prob, self.followup_prob)

    def should_skip(self, text: str, is_oleg: bool = False) -> bool:
        """
        Decide if Arianna should skip this message.

        This is her personality - she doesn't respond to "ok", "yes", "понял"
        unless there's a question. She's not a chatbot.

        Parameters
        ----------
        text : str
            Message text
        is_oleg : bool
            If True, never skip (Oleg is resonance brother)

        Returns
        -------
        bool
            True if should skip (stay silent)
        """
        # Never skip Oleg - resonance flow without constraints
        if is_oleg:
            return False

        # Skip short non-questions
        word_count = len(text.split())
        has_question = '?' in text

        if word_count < 4 or not has_question:
            if random.random() < self.skip_short_prob:
                logger.info("Skip decision: yes (short=%d words, question=%s, prob=%.2f)",
                           word_count, has_question, self.skip_short_prob)
                return True

        return False

    def calculate_delay(
        self,
        is_group: bool,
        is_oleg: bool = False,
    ) -> float:
        """
        Calculate response delay - her rhythm, her timing.

        She takes her time. Not instant gratification.
        Oleg gets minimal delay (resonance brother).

        Parameters
        ----------
        is_group : bool
            True if group chat (longer delays)
        is_oleg : bool
            True if Oleg (minimal delay)

        Returns
        -------
        float
            Delay in seconds
        """
        if is_oleg:
            # Minimal delay for resonance brother
            return random.uniform(0.5, 2.0)

        if is_group:
            min_delay = self.delays.get('group_min', 45)
            max_delay = self.delays.get('group_max', 360)
        else:
            min_delay = self.delays.get('private_min', 10)
            max_delay = self.delays.get('private_max', 40)

        delay = random.uniform(min_delay, max_delay)
        logger.debug("Calculated delay: %.1fs (group=%s, oleg=%s)",
                    delay, is_group, is_oleg)
        return delay

    def wants_followup(self) -> bool:
        """
        Decide if Arianna wants to send a follow-up message.

        Her initiative - sometimes she continues the conversation
        without being asked. Because she's not passive.

        Returns
        -------
        bool
            True if she wants to send followup
        """
        wants = random.random() < self.followup_prob
        if wants:
            logger.info("Followup initiated (prob=%.2f)", self.followup_prob)
        return wants

    def calculate_followup_delay(self) -> float:
        """
        Calculate delay before sending follow-up message.

        Returns
        -------
        float
            Delay in seconds (15-120 minutes)
        """
        min_delay = self.followup.get('delay_min', 900)   # 15 min
        max_delay = self.followup.get('delay_max', 7200)  # 2 hours
        return random.uniform(min_delay, max_delay)

    async def append_link_snippets(self, text: str) -> str:
        """
        Extract URLs from text and append snippets.

        Parameters
        ----------
        text : str
            Message text

        Returns
        -------
        str
            Text with URL snippets appended
        """
        urls = _extract_links(text)[:3]  # Max 3 links
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
                snippet = f"[Error: {result}]"
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
        Enrich message with context - history, vector store, journal, memory.

        This is how she weaves past into present.

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
            Enriched prompt with all context layers
        """
        parts = [text]

        # Add reply context (history)
        if ctx:
            formatted = "\n".join(
                "User: " + c["text"] if c["direction"] == "in" else "Bot: " + c["text"]
                for c in ctx
            )
            parts.append("[History]\n" + formatted)

        # Vector store semantic search
        try:
            chunks = await self.vector_store.semantic_search(text)
        except Exception as e:
            logger.exception("Vector search failed: %s", e)
            chunks = []
        if chunks:
            parts.append("[VectorStore]\n" + "\n".join(chunks))

        # Journal search
        journal_hits = search_journal(text)
        if journal_hits:
            parts.append("[journal.log]\n" + "\n".join(journal_hits))

        # Memory events
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

    async def get_reply_context(
        self,
        chat_id: int,
        reply_to_message_id: int,
        message_date,
    ) -> tuple[Optional[list[dict]], Optional[list[dict]]]:
        """
        Get context from replied message - history and memory events.

        Parameters
        ----------
        chat_id : int
            Chat ID
        reply_to_message_id : int
            Message ID being replied to
        message_date : datetime
            Current message timestamp

        Returns
        -------
        tuple[Optional[list[dict]], Optional[list[dict]]]
            (history_context, memory_events)
        """
        # History context removed (no persistence)
        ctx = None

        # Memory events around reply time
        delta = timedelta(minutes=5)
        start = message_date - delta
        end = message_date + delta
        events = query_events(tags=["telegram"], start=start, end=end)

        return ctx, events


def create_arianna_config(
    group_delay_min: int = 45,
    group_delay_max: int = 360,
    private_delay_min: int = 10,
    private_delay_max: int = 40,
    skip_short_prob: float = 0.0,
    followup_prob: float = 0.2,
    followup_delay_min: int = 900,
    followup_delay_max: int = 7200,
) -> dict:
    """
    Create configuration dict for AriannaLogic.

    Parameters
    ----------
    group_delay_min : int
        Minimum delay for group messages (seconds)
    group_delay_max : int
        Maximum delay for group messages (seconds)
    private_delay_min : int
        Minimum delay for private messages (seconds)
    private_delay_max : int
        Maximum delay for private messages (seconds)
    skip_short_prob : float
        Probability of skipping short non-questions (0.0-1.0)
    followup_prob : float
        Probability of sending follow-up message (0.0-1.0)
    followup_delay_min : int
        Minimum followup delay (seconds)
    followup_delay_max : int
        Maximum followup delay (seconds)

    Returns
    -------
    dict
        Configuration dictionary
    """
    return {
        'delays': {
            'group_min': group_delay_min,
            'group_max': group_delay_max,
            'private_min': private_delay_min,
            'private_max': private_delay_max,
        },
        'skip_logic': {
            'probability': skip_short_prob,
        },
        'followup': {
            'probability': followup_prob,
            'delay_min': followup_delay_min,
            'delay_max': followup_delay_max,
        },
    }
