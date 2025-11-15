"""
Arianna Core - Interface-independent logic

This package contains the core Arianna engine that can be used
with any interface (Telegram, daemon, API, SSH bridges).
"""

__version__ = "7.0"

from core.engine import AriannaCoreEngine, web_search, handle_tool_call
from core.vector_store_sqlite import SQLiteVectorStore, VectorStore

__all__ = [
    'AriannaCoreEngine',
    'web_search',
    'handle_tool_call',
    'SQLiteVectorStore',
    'VectorStore',
]
