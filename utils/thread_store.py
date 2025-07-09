import os
import json

THREADS_PATH = "data/threads.json"


def load_threads(path: str = THREADS_PATH) -> dict:
    """Load stored thread mappings from JSON."""
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_threads(threads: dict, path: str = THREADS_PATH) -> None:
    """Save thread mappings to JSON."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(threads, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
