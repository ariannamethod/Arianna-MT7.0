"""
ARIANNA MT7.0 PROMPT
Backward compatibility wrapper - imports from prompt_mt7

DEPRECATED: This module is kept for backward compatibility.
Use prompt_mt7 directly for new code.
"""

import warnings
from utils.prompt_mt7 import (
    ARIANNA_MT7_PROMPT,
    build_system_prompt_mt7,
    build_system_prompt,
)

# Show deprecation warning on import
warnings.warn(
    "utils.prompt is deprecated. Import from utils.prompt_mt7 instead.",
    DeprecationWarning,
    stacklevel=2
)

__all__ = [
    'ARIANNA_MT7_PROMPT',
    'build_system_prompt_mt7',
    'build_system_prompt',
]
