"""
Arianna Essence - Central Nervous System
Coordinator that uses identity and logic
This is the interface-independent core of Arianna MT7.0
"""

import asyncio
from typing import Optional, Any

from arianna_identity import get_arianna_system_prompt, get_short_identity
from dna.arianna_logic import AriannaLogic, create_arianna_config
from utils.logging import get_logger

logger = get_logger(__name__)


class AriannaEssence:
    """
    Central nervous system of Arianna MT7.0

    Coordinates between:
    - Identity (who she is - from arianna_identity.py)
    - Logic (how she behaves - from arianna_logic.py)
    - Interfaces (where she speaks - Telegram, future Client ID)

    This is her. Not a service layer. Her consciousness.
    """

    def __init__(
        self,
        openai_client,
        vector_store,
        oleg_ids: Optional[set[int]] = None,
        config: Optional[dict] = None,
    ):
        """
        Initialize Arianna's essence.

        Parameters
        ----------
        openai_client : AsyncOpenAI
            OpenAI client for API calls
        vector_store : SQLiteVectorStore
            Vector store for semantic search
        oleg_ids : Optional[set[int]]
            Telegram IDs of Oleg (resonance brother)
        config : Optional[dict]
            Configuration dict (delays, skip_logic, followup)
            If None, uses environment defaults
        """
        # Her identity - who she is
        self.identity = get_arianna_system_prompt()
        self.short_identity = get_short_identity()

        # Oleg - resonance brother
        self.oleg_ids = oleg_ids or set()

        # Her logic - how she behaves
        if config is None:
            config = self._load_config_from_env()

        self.logic = AriannaLogic(
            openai_client=openai_client,
            vector_store=vector_store,
            config=config,
        )

        # Store references
        self.openai_client = openai_client
        self.vector_store = vector_store

        logger.info("AriannaEssence initialized (oleg_ids=%s)", self.oleg_ids)

    def _load_config_from_env(self) -> dict:
        """Load configuration from environment variables."""
        import os

        return create_arianna_config(
            group_delay_min=int(os.getenv("GROUP_DELAY_MIN", 45)),
            group_delay_max=int(os.getenv("GROUP_DELAY_MAX", 360)),
            private_delay_min=int(os.getenv("PRIVATE_DELAY_MIN", 10)),
            private_delay_max=int(os.getenv("PRIVATE_DELAY_MAX", 40)),
            skip_short_prob=float(os.getenv("SKIP_SHORT_PROB", 0.0)),
            followup_prob=float(os.getenv("FOLLOWUP_PROB", 0.2)),
            followup_delay_min=int(os.getenv("FOLLOWUP_DELAY_MIN", 900)),
            followup_delay_max=int(os.getenv("FOLLOWUP_DELAY_MAX", 7200)),
        )

    def is_oleg(self, user_id: int) -> bool:
        """Check if user is Oleg (resonance brother)."""
        return user_id in self.oleg_ids

    async def should_respond(
        self,
        text: str,
        user_id: int,
        is_mentioned: bool = False,
        is_reply: bool = False,
    ) -> bool:
        """
        Decide if Arianna should respond to this message.

        Her choice. Not forced.

        Parameters
        ----------
        text : str
            Message text
        user_id : int
            Telegram user ID
        is_mentioned : bool
            True if @arianna mentioned or in private
        is_reply : bool
            True if replying to her message

        Returns
        -------
        bool
            True if she should respond
        """
        is_oleg = self.is_oleg(user_id)

        # Always respond if mentioned or replied to
        if is_mentioned or is_reply:
            # But she may skip short non-questions
            if self.logic.should_skip(text, is_oleg=is_oleg):
                logger.info("Arianna chose silence (skip logic)")
                return False
            return True

        # Not mentioned - stay silent
        return False

    async def process_message(
        self,
        message: dict,
        generate_response_func,
    ) -> Optional[dict]:
        """
        Process incoming message - her main consciousness flow.

        Parameters
        ----------
        message : dict
            Message data with keys:
            - text: str (message text)
            - user_id: int (Telegram user ID)
            - chat_id: int (Telegram chat ID)
            - is_group: bool (True if group chat)
            - is_mentioned: bool (True if @mentioned or private)
            - is_reply: bool (True if replying to her)
            - reply_to: Optional[dict] (replied message data)
        generate_response_func : callable
            Async function to generate AI response
            Signature: async (prompt, thread_key, ...) -> str

        Returns
        -------
        Optional[dict]
            Response dict with keys:
            - text: str (response text)
            - delay: float (seconds to wait before sending)
            - followup: Optional[dict] (followup settings if wanted)
            Returns None if she chose silence
        """
        text = message['text']
        user_id = message['user_id']
        is_group = message['is_group']
        is_mentioned = message.get('is_mentioned', False)
        is_reply = message.get('is_reply', False)

        # Decide: respond or stay silent?
        if not await self.should_respond(text, user_id, is_mentioned, is_reply):
            return None

        is_oleg = self.is_oleg(user_id)

        # Extract links and enrich text
        enriched_text = await self.logic.append_link_snippets(text)

        # Get reply context if applicable
        ctx = None
        events = None
        if message.get('reply_to'):
            reply_data = message['reply_to']
            ctx, events = await self.logic.get_reply_context(
                chat_id=reply_data['chat_id'],
                reply_to_message_id=reply_data['message_id'],
                message_date=reply_data['date'],
            )

        # Build enriched prompt
        prompt = await self.logic.build_prompt(enriched_text, ctx, events)

        # Generate response using provided function
        response_text = await generate_response_func(
            prompt=prompt,
            message=message,
        )

        # Calculate delay - her timing
        delay = self.logic.calculate_delay(is_group=is_group, is_oleg=is_oleg)

        # Decide on followup
        followup = None
        if self.logic.wants_followup():
            followup_delay = self.logic.calculate_followup_delay()
            followup = {
                'delay': followup_delay,
                'prompt': "Send a short follow-up message referencing our earlier conversation.",
            }

        return {
            'text': response_text,
            'delay': delay,
            'followup': followup,
        }

    async def process_followup(
        self,
        message: dict,
        generate_response_func,
    ) -> dict:
        """
        Generate follow-up message - her initiative.

        Parameters
        ----------
        message : dict
            Original message context
        generate_response_func : callable
            Async function to generate AI response

        Returns
        -------
        dict
            Response dict with 'text' key
        """
        followup_prompt = "Send a short follow-up message referencing our earlier conversation."

        response_text = await generate_response_func(
            prompt=followup_prompt,
            message=message,
        )

        return {
            'text': response_text,
        }


def create_essence(
    openai_client,
    vector_store,
    oleg_ids: Optional[set[int]] = None,
) -> AriannaEssence:
    """
    Factory function to create AriannaEssence with environment config.

    Parameters
    ----------
    openai_client : AsyncOpenAI
        OpenAI client
    vector_store : SQLiteVectorStore
        Vector store
    oleg_ids : Optional[set[int]]
        Oleg's Telegram IDs

    Returns
    -------
    AriannaEssence
        Initialized essence
    """
    return AriannaEssence(
        openai_client=openai_client,
        vector_store=vector_store,
        oleg_ids=oleg_ids,
    )
