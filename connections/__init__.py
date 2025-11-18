"""
Connections - External Integrations & Advanced Features

This module contains utilities for connecting Arianna to external systems,
advanced reasoning engines, and perception layers:

- vision.py: Image perception through GPT-4 Vision
- imagine.py: Image generation (DALL-E/other)
- genesis_tool.py: Genesis-1 reasoning tool
- newgenesis.py: Genesis-2 intuitive layer (legacy)
- newgenesis2.py: Genesis-2 intuitive layer (current)
- context_neural_processor.py: Context processing with DeepSeek R1
- memory.py: Event memory and semantic query
- repo_monitor.py: Repository change detection and auto-reindexing
- state_snapshot.py: Periodic state snapshots for recovery
"""

from .vision import perceive_image
from .imagine import imagine
from .genesis_tool import genesis_tool_schema, handle_genesis_call
from .memory import add_event, query_events, semantic_query

__all__ = [
    'perceive_image',
    'imagine',
    'genesis_tool_schema',
    'handle_genesis_call',
    'add_event',
    'query_events',
    'semantic_query',
]
