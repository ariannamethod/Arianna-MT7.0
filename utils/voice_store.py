import os
import json

from .atomic_json import atomic_json_dump

VOICE_PATH = "data/voice.json"


def load_voice_state(path: str = VOICE_PATH) -> dict:
    """Load stored voice mode mappings from JSON."""
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_voice_state(state: dict, path: str = VOICE_PATH) -> None:
    """Save voice mode mappings to JSON."""
    try:
        atomic_json_dump(path, state, ensure_ascii=False, indent=2)
    except Exception:
        pass
