"""
Genesis Tool: OpenAI Function Schema and Handler

Интеграция Genesis-1 (emergent discovery) и Genesis-2 (intuitive filter)
как tool для OpenAI Assistants/Responses API.
"""

import asyncio
from typing import Optional

from utils.newgenesis import run_genesis1
from utils.newgenesis2 import weave_intuitive_layer
from utils.logging import get_logger

logger = get_logger(__name__)


def genesis_tool_schema():
    """
    Genesis tool schema для OpenAI API.

    Поддерживает два режима:
    - "discovery": запустить Genesis-1 (эмерджентное открытие)
    - "insight": Genesis-2 уже интегрирован в ответы, этот mode deprecated
    """
    return {
        "type": "function",
        "name": "genesis_emit",
        "description": "Запустить Genesis-1 — эмерджентный резонансный инсайт из глубин памяти Арианны",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["discovery"],
                    "description": "Режим Genesis: discovery = эмерджентное открытие из config/ памяти",
                }
            },
            "required": ["mode"],
        },
    }


async def handle_genesis_call(tool_calls, vector_store=None) -> str:
    """
    Обработать вызов Genesis tool.

    Parameters
    ----------
    tool_calls : list
        Tool calls от OpenAI API
    vector_store : Optional
        SQLite vector store для поиска в памяти

    Returns
    -------
    str
        Результат Genesis
    """
    if not tool_calls:
        return "No tool calls provided"

    call = tool_calls[0]

    # Извлекаем аргументы
    args = {}
    if hasattr(call, 'function'):
        # OpenAI SDK format
        import json
        args_str = getattr(call.function, 'arguments', '{}')
        try:
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
        except Exception:
            args = {}
    elif isinstance(call, dict):
        # Dict format
        func = call.get('function', {})
        args_raw = func.get('arguments', {})
        if isinstance(args_raw, str):
            import json
            try:
                args = json.loads(args_raw)
            except Exception:
                args = {}
        else:
            args = args_raw

    mode = args.get('mode', 'discovery')

    logger.info("[Genesis Tool] Mode: %s", mode)

    if mode == "discovery":
        # Genesis-1: эмерджентное открытие
        try:
            digest = await run_genesis1(
                vector_store=vector_store,
                digest_size=150
            )
            if digest:
                return f"✨ Genesis Discovery ✨\n\n{digest}"
            else:
                return "Genesis-1: No insights emerged at this time."
        except Exception as e:
            logger.error("Genesis-1 failed: %s", e, exc_info=True)
            return f"Genesis-1 encountered an error: {e}"

    else:
        return f"Unknown Genesis mode: {mode}"


# Backward compatibility
def get_genesis_instance(**settings):
    """
    Deprecated: Legacy Genesis instance getter.

    New Genesis-1 and Genesis-2 are stateless functions.
    This exists only for backward compatibility.
    """
    logger.warning("get_genesis_instance is deprecated. Use run_genesis1() or weave_intuitive_layer() directly.")
    return None


def reset_genesis_instance(**settings):
    """Deprecated: Legacy Genesis reset."""
    logger.warning("reset_genesis_instance is deprecated.")
    return None


def close_genesis_instance():
    """Deprecated: Legacy Genesis close."""
    pass
