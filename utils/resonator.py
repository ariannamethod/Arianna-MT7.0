import os
import random
import calendar
import datetime
from typing import List


_DATA_DIR = os.path.abspath(os.getenv("SUPPERTIME_DATA_PATH", "./data/chapters"))


def get_all_chapter_files() -> List[str]:
    """Return sorted Markdown files from ``SUPPERTIME_DATA_PATH``."""
    path = _DATA_DIR
    if not os.path.isdir(path):
        return []
    files = [f for f in os.listdir(path) if f.lower().endswith(".md")]
    return sorted(os.path.join(path, f) for f in files)


def get_monthly_plan(year: int, month: int) -> List[str]:
    """Return list of chapter paths for each day of the month."""
    chapters = get_all_chapter_files()
    if not chapters:
        return []
    rng = random.Random(f"{year:04d}-{month:02d}")
    rng.shuffle(chapters)
    num_days = calendar.monthrange(year, month)[1]
    return [chapters[i % len(chapters)] for i in range(num_days)]


def load_today_chapter(return_path: bool = False) -> str:
    """Load today's chapter text or return an error message."""
    today = datetime.date.today()
    plan = get_monthly_plan(today.year, today.month)
    if not plan:
        return "[No chapters found]"
    path = plan[today.day - 1]
    if return_path:
        return path if os.path.isfile(path) else f"[Missing: {os.path.basename(path)}]"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"[Error reading {os.path.basename(path)}: {e}]"

