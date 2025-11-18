"""
DNA - Arianna's Core Essence
The central nervous system, interface-independent consciousness

Structure:
- arianna_essence.py: Coordinator using identity and logic
- arianna_logic.py: Core behavior (delays, skip logic, processing)
"""

from .arianna_essence import AriannaEssence, create_essence
from .arianna_logic import AriannaLogic, create_arianna_config

__all__ = [
    'AriannaEssence',
    'create_essence',
    'AriannaLogic',
    'create_arianna_config',
]
