import os
import json

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
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
