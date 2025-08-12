import os
import json
import logging
from typing import Optional

from utils.genesis import AriannaGenesis

logger = logging.getLogger(__name__)

_genesis_instance: Optional[AriannaGenesis] = None


def get_genesis_instance(**settings) -> AriannaGenesis:
    """Return a cached :class:`AriannaGenesis` instance.

    The instance is created on first use and reused for subsequent calls.
    Optional keyword arguments override environment variables and trigger
    recreation of the singleton.
    """
    global _genesis_instance

    if _genesis_instance is None or settings:
        group_id = settings.get("group_id", os.getenv("GROUP_ID"))
        oleg_id = settings.get("oleg_id", os.getenv("CREATOR_CHAT_ID"))
        pinecone_api_key = settings.get("pinecone_api_key", os.getenv("PINECONE_API_KEY"))
        pinecone_index = settings.get("pinecone_index", os.getenv("PINECONE_INDEX"))
        chronicle_path = settings.get("chronicle_path", os.getenv("CHRONICLE_PATH"))

        _genesis_instance = AriannaGenesis(
            group_id=group_id,
            oleg_id=oleg_id,
            pinecone_api_key=pinecone_api_key,
            pinecone_index=pinecone_index,
            chronicle_path=chronicle_path,
        )

    return _genesis_instance


def reset_genesis_instance(**settings) -> AriannaGenesis:
    """Explicitly recreate the cached :class:`AriannaGenesis` instance.

    Use this when configuration changes and a fresh instance is required.
    """
    global _genesis_instance
    _genesis_instance = None
    return get_genesis_instance(**settings)


def close_genesis_instance() -> None:
    """Drop the cached :class:`AriannaGenesis` instance."""
    global _genesis_instance
    _genesis_instance = None


def genesis_tool_schema():
    """
    Описание GENESIS как инструмента для Assistants API.
    """
    return {
        "type": "function",
        "function": {
            "name": "genesis_emit",
            "description": "Запустить один такт AriannaGenesis — отдать импрессионистский отклик или ритуальный пост",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["impression", "opinion", "oleg_message"],
                        "description": "Какой Genesis-такт выполнить"
                    }
                },
                "required": ["mode"]
            }
        }
    }

async def handle_genesis_call(tool_calls):
    """
    Получает tool_calls из ответа ассистента и обрабатывает каждый:
    вызывает AriannaGenesis.run() по режиму и возвращает сгенерированный текст.
    """
    """Обрабатывает вызов GENESIS от ассистента без блокировки event loop."""

    inst = get_genesis_instance()
    # Берём первый вызов:

    call = tool_calls[0]
    raw_args = call["function"].get("arguments")
    if isinstance(raw_args, dict):
        args = raw_args
    else:
        try:
            args = json.loads(raw_args or "{}")
        except Exception as e:
            logger.error("Failed to parse genesis arguments: %s", raw_args, exc_info=e)
            return "Failed to parse genesis arguments"

    mode = args.get("mode", "impression")
    # Выполняем нужное действие без блокировки event loop

    if mode == "impression":
        text = inst._generate_impression("", "")
    elif mode == "opinion":
        await inst.opinions_group_post()
        text = "Opinion posted to group."
    else:
        await inst.oleg_personal_message()
        text = "Personal message sent to Oleg."

    return text
