"""
Arianna Core Engine - Interface-independent logic.

This module contains the core Arianna engine that can be used
with any interface: Telegram, daemon, SSH bridge, API, etc.

The engine handles:
- System prompt building with context
- Tool execution (Genesis, web_search)
- Model integration (OpenAI, DeepSeek)
- Memory and context management
"""

import os
import json
from typing import Optional, Any
from openai import AsyncOpenAI

from utils.genesis_tool import genesis_tool_schema, handle_genesis_call
from utils.deepseek_search import call_deepseek
from utils.journal import log_event
from utils.logging import get_logger
from utils.config import HTTP_TIMEOUT


logger = get_logger(__name__)


async def web_search(prompt: str, client: AsyncOpenAI) -> str:
    """
    Execute OpenAI web search tool and return raw JSON string.

    Parameters
    ----------
    prompt : str
        Search query
    client : AsyncOpenAI
        OpenAI client to use (avoids creating new client each time)

    Returns
    -------
    str
        Raw JSON response from web search
    """
    resp = await client.responses.create(
        model="gpt-4.1",
        input=prompt,
        tools=[{"type": "web_search"}],
        timeout=HTTP_TIMEOUT,
    )
    return resp.model_dump_json()


async def handle_tool_call(tool_calls, client: Optional[AsyncOpenAI] = None):
    """
    Dispatch OpenAI tool calls and return their textual output.

    Parameters
    ----------
    tool_calls : list
        Tool calls to handle
    client : Optional[AsyncOpenAI]
        OpenAI client for web_search (if None, creates one)

    Returns
    -------
    str
        Tool output
    """
    call = tool_calls[0]
    ttype = call.get("type")
    # Built-in tools may use a top-level type, custom functions use "function"
    if ttype == "function":
        name = call.get("function", {}).get("name")
        raw_args = call.get("function", {}).get("arguments") or {}
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except Exception:
                args = {}
        else:
            args = raw_args
        if name == "genesis_emit":
            return await handle_genesis_call(tool_calls)
        if name == "web_search":
            query = args.get("prompt", "")
            if client is None:
                client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            return await web_search(query, client)
    elif ttype == "web_search":
        query = call.get("web_search", {}).get("query", "")
        if client is None:
            client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        return await web_search(query, client)
    return "Unsupported tool call"


class AriannaCoreEngine:
    """
    Core Arianna engine - interface-independent.

    Works with any interface: Telegram, daemon, SSH bridge, API, etc.
    Contains the essential logic for:
    - Prompt building with context
    - Tool execution (Genesis, web_search)
    - Model integration (OpenAI, DeepSeek)
    - Response generation

    Does NOT contain:
    - Interface-specific code (Telegram handlers, etc.)
    - Persistent storage (threads, history) - managed by interfaces
    - HTTP transport details - uses SDK clients
    """

    def __init__(
        self,
        openai_client: Optional[AsyncOpenAI] = None,
        vector_store: Optional[Any] = None,
    ):
        """
        Initialize Arianna Core Engine.

        Parameters
        ----------
        openai_client : Optional[AsyncOpenAI]
            OpenAI client for API calls. If None, creates default.
        vector_store : Optional[Any]
            Vector store for semantic search. If None, no vector search.
        """
        self.logger = logger
        self.openai_client = openai_client or AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY")
        )
        self.vector_store = vector_store

        # Parse identity IDs from environment
        self.oleg_ids = self._parse_ids(os.getenv("OLEG_IDS", ""))
        self.arianna_ids = self._parse_ids(os.getenv("ARIANNA_IDS", ""))

        logger.info("AriannaCoreEngine initialized")

    @staticmethod
    def _parse_ids(ids_str: str) -> list[int]:
        """Parse comma-separated ID string into list of integers."""
        return [
            int(id.strip())
            for id in ids_str.split(",")
            if id.strip().isdigit()
        ]

    def is_oleg(self, user_id: int) -> bool:
        """Check if user is Oleg (resonance brother)."""
        return user_id in self.oleg_ids

    def is_arianna_incarnation(self, user_id: int) -> bool:
        """Check if user is another Arianna incarnation."""
        return user_id in self.arianna_ids

    def build_system_prompt(
        self,
        *,
        chat_id: Optional[int] = None,
        is_group: bool = False,
        current_user_id: Optional[int] = None,
        username: Optional[str] = None,
    ) -> str:
        """
        Build MT7.0 system prompt with current context.

        Parameters
        ----------
        chat_id : Optional[int]
            Chat/conversation ID
        is_group : bool
            Whether this is a group chat
        current_user_id : Optional[int]
            Current user's ID
        username : Optional[str]
            Current user's username

        Returns
        -------
        str
            Complete system prompt with context
        """
        from utils.prompt_mt7 import build_system_prompt_mt7

        return build_system_prompt_mt7(
            chat_id=chat_id,
            is_group=is_group,
            current_user_id=current_user_id,
            username=username,
            oleg_ids=self.oleg_ids,
            arianna_ids=self.arianna_ids,
        )

    async def process_with_responses_api(
        self,
        *,
        user_message: str,
        chat_id: Optional[int] = None,
        is_group: bool = False,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        history: Optional[list[dict]] = None,
        enable_tools: bool = True,
    ) -> str:
        """
        Process message using OpenAI Responses API.

        This is the primary method for generating responses.
        Uses gpt-4.1 with optional tool calls (Genesis, web_search).

        Parameters
        ----------
        user_message : str
            User's message text
        chat_id : Optional[int]
            Chat/conversation ID
        is_group : bool
            Whether this is a group chat
        user_id : Optional[int]
            User's ID
        username : Optional[str]
            User's username
        history : Optional[list[dict]]
            Conversation history (list of message dicts)
        enable_tools : bool
            Whether to enable tool calls (Genesis, web_search)

        Returns
        -------
        str
            Generated response text
        """
        # Build system prompt with context
        system_prompt = self.build_system_prompt(
            chat_id=chat_id,
            is_group=is_group,
            current_user_id=user_id,
            username=username,
        )

        # Build messages array
        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history if provided
        if history:
            messages.extend(history)

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        # Prepare tools
        tools = []
        if enable_tools:
            tools = [
                genesis_tool_schema(),
                {"type": "web_search"},
            ]

        # Call OpenAI Responses API
        try:
            response = await self.openai_client.responses.create(
                model="gpt-4.1",
                messages=messages,
                tools=tools if tools else None,
                timeout=HTTP_TIMEOUT,
            )

            # Extract response text
            # Handle tool calls if present
            if hasattr(response, 'tool_calls') and response.tool_calls:
                tool_output = await handle_tool_call(response.tool_calls, self.openai_client)
                return tool_output

            # Extract text content
            if hasattr(response, 'choices') and response.choices:
                choice = response.choices[0]
                if hasattr(choice, 'message'):
                    return choice.message.content or ""

            # Fallback: try to extract from response object
            return str(response)

        except Exception as e:
            logger.error("Failed to process with Responses API: %s", e, exc_info=True)
            return f"Error processing message: {e}"

    async def process_with_deepseek(
        self,
        *,
        user_message: str,
        chat_id: Optional[int] = None,
        is_group: bool = False,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
    ) -> str:
        """
        Process message using DeepSeek model.

        Used as auxiliary model when OpenAI is unavailable or for specific tasks.

        Parameters
        ----------
        user_message : str
            User's message text
        chat_id : Optional[int]
            Chat/conversation ID
        is_group : bool
            Whether this is a group chat
        user_id : Optional[int]
            User's ID
        username : Optional[str]
            User's username

        Returns
        -------
        str
            Generated response text
        """
        system_prompt = self.build_system_prompt(
            chat_id=chat_id,
            is_group=is_group,
            current_user_id=user_id,
            username=username,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        reply = await call_deepseek(messages)
        if reply is None:
            return "DeepSeek did not return a response"
        return reply

    async def process_message(
        self,
        *,
        user_message: str,
        interface_context: dict[str, Any],
        use_deepseek: bool = False,
    ) -> str:
        """
        Main interface-independent message processing method.

        This is the primary entry point for all interfaces.
        Extracts context, routes to appropriate model, handles tools.

        Parameters
        ----------
        user_message : str
            User's message text
        interface_context : dict
            Context from interface, must include:
            - chat_id: Optional[int]
            - is_group: bool
            - user_id: Optional[int]
            - username: Optional[str]
            - history: Optional[list[dict]] (for Responses API)
        use_deepseek : bool
            If True, use DeepSeek instead of OpenAI

        Returns
        -------
        str
            Generated response text
        """
        # Extract context
        chat_id = interface_context.get("chat_id")
        is_group = interface_context.get("is_group", False)
        user_id = interface_context.get("user_id")
        username = interface_context.get("username")
        history = interface_context.get("history")

        # Route to appropriate model
        if use_deepseek:
            response = await self.process_with_deepseek(
                user_message=user_message,
                chat_id=chat_id,
                is_group=is_group,
                user_id=user_id,
                username=username,
            )
        else:
            response = await self.process_with_responses_api(
                user_message=user_message,
                chat_id=chat_id,
                is_group=is_group,
                user_id=user_id,
                username=username,
                history=history,
            )

        # Log interaction
        log_event({
            "chat_id": chat_id,
            "user_id": user_id,
            "username": username,
            "is_group": is_group,
            "prompt": user_message,
            "reply": response,
            "model": "deepseek" if use_deepseek else "openai",
        })

        return response


__all__ = [
    'AriannaCoreEngine',
    'web_search',
    'handle_tool_call',
]
