"""
State Snapshotter - SQLite-based daily state collection

Collects daily snapshots of memory, logs, and resources.
Stores embeddings in SQLite instead of Pinecone.
"""

import asyncio
import datetime
import json
import math
import os
import sqlite3
import threading
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
try:  # pragma: no cover - optional dependency
    from scipy.stats import entropy as scipy_entropy  # type: ignore
except Exception:  # pragma: no cover - no scipy installed
    scipy_entropy = None  # type: ignore

from utils.atomic_json import atomic_json_dump
from utils.logging import get_logger
from utils.memory import add_event

logger = get_logger(__name__)

EMBED_DIM = 1536  # OpenAI ada-002
_thread_local = threading.local()


class StateSnapshotter:
    """
    Collect daily state, embed it and store in SQLite and local JSON.

    Migrated from Pinecone to SQLite for local storage.
    """

    def __init__(
        self,
        *,
        chronicle_path: Optional[str] = None,
        local_store: str = "data/state_vectors.json",
        sqlite_db: str = "data/state_snapshots.db",
        openai_client: Optional[AsyncOpenAI] = None,
        openai_api_key: Optional[str] = None,
    ) -> None:
        self.chronicle_path = (
            chronicle_path
            or os.getenv("CHRONICLE_PATH", "config/chronicle.log")
        )
        self.local_store = Path(local_store)
        self.sqlite_db = Path(sqlite_db)
        self.openai_client = openai_client or AsyncOpenAI(
            api_key=openai_api_key or os.getenv("OPENAI_API_KEY")
        )

        # Initialize SQLite for snapshots
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local SQLite connection."""
        if not hasattr(_thread_local, 'conn') or _thread_local.conn is None:
            self.sqlite_db.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.sqlite_db), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            _thread_local.conn = conn
            logger.debug("Created new SQLite connection for snapshots")
        return _thread_local.conn

    def _init_db(self) -> None:
        """Initialize SQLite database for state snapshots."""
        conn = self._get_connection()
        try:
            conn.executescript("""
                -- State snapshots table
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    block TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    entropy REAL,
                    perplexity REAL,
                    embedding BLOB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- Index for querying by block and timestamp
                CREATE INDEX IF NOT EXISTS idx_snapshots_block_ts
                ON snapshots(block, timestamp);
            """)
            conn.commit()
            logger.debug("State snapshots database initialized")
        except Exception as e:
            logger.error("Failed to initialize snapshots database: %s", e, exc_info=True)
            raise

    # ------------------------------------------------------------------
    # State collection
    # ------------------------------------------------------------------
    def _read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Text analytics helpers
    # ------------------------------------------------------------------
    def _calc_metrics(self, text: str) -> Dict[str, float]:
        tokens = [t for t in text.split() if t]
        if len(tokens) < 2:
            return {"entropy": 0.0, "perplexity": 0.0}
        bigrams = list(zip(tokens[:-1], tokens[1:]))
        counts = Counter(bigrams)
        total = sum(counts.values())
        probs = [c / total for c in counts.values()]
        if scipy_entropy:
            ent = float(scipy_entropy(probs))
        else:
            ent = float(-sum(p * math.log(p) for p in probs if p))
        pp = float(math.exp(ent))
        return {"entropy": ent, "perplexity": pp}

    def collect_state(self) -> Dict[str, Dict[str, Any]]:
        memory = self._read_file(Path("data/journal.json"))
        logs = self._read_file(Path(self.chronicle_path))
        resources = "\n".join(
            line.strip() for line in logs.splitlines() if "http" in line
        )
        result = {}
        for name, text in {
            "memory": memory,
            "logs": logs,
            "resources": resources,
        }.items():
            result[name] = {"text": text, **self._calc_metrics(text)}
        return result

    # ------------------------------------------------------------------
    # Local store helpers
    # ------------------------------------------------------------------
    def _load_local(self) -> List[Dict[str, Any]]:
        if self.local_store.exists():
            try:
                with open(self.local_store, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:  # pragma: no cover - corrupted file
                return []
        return []

    def _save_local(self, data: List[Dict[str, Any]]) -> None:
        self.local_store.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_dump(str(self.local_store), data)

    # ------------------------------------------------------------------
    # Math helpers
    # ------------------------------------------------------------------
    def _cosine(self, a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if not na or not nb:
            return 0.0
        return dot / (na * nb)

    async def _embed_text(self, text: str) -> List[float]:
        if not text.strip():
            return [0.0] * EMBED_DIM
        try:
            res = await self.openai_client.embeddings.create(
                model="text-embedding-ada-002",
                input=text,
            )
            return res.data[0].embedding
        except Exception as e:  # pragma: no cover - logging only
            logger.warning("Embedding failed: %s", e)
            return [0.0] * EMBED_DIM

    def _store_snapshot_sqlite(
        self,
        snapshot_id: str,
        block: str,
        timestamp: str,
        embedding: List[float],
        entropy: float,
        perplexity: float
    ) -> None:
        """Store snapshot in SQLite."""
        conn = self._get_connection()
        try:
            # Convert embedding to bytes
            import array
            emb_array = array.array('f', embedding)
            emb_bytes = emb_array.tobytes()

            conn.execute(
                """
                INSERT OR REPLACE INTO snapshots
                (id, block, timestamp, entropy, perplexity, embedding)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (snapshot_id, block, timestamp, entropy, perplexity, emb_bytes)
            )
            conn.commit()
        except Exception as e:
            logger.error("Failed to store snapshot in SQLite: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def run_once(self) -> None:
        """Run a single snapshot collection cycle."""
        state = self.collect_state()
        timestamp = datetime.datetime.now().isoformat()
        local_data = self._load_local()

        for block, data in state.items():
            text = data.get("text", "")
            ent = data.get("entropy", 0.0)
            pp = data.get("perplexity", 0.0)
            emb = await self._embed_text(text)
            snapshot_id = f"{timestamp}:{block}"

            # Store in SQLite
            await asyncio.to_thread(
                self._store_snapshot_sqlite,
                snapshot_id, block, timestamp, emb, ent, pp
            )

            # Calculate similarity to previous snapshot
            prev = next(
                (e for e in reversed(local_data) if e.get("block") == block),
                None,
            )
            if prev:
                sim = self._cosine(prev.get("embedding", []), emb)
                logger.info(
                    "Snapshot %s similarity to previous: %.3f", block, sim
                )
            else:
                sim = 0.0
                logger.info("Snapshot %s recorded", block)

            summary = f"{block} similarity={sim:.2f} perplexity={pp:.2f}"
            add_event("snapshot_summary", summary, tags=["snapshot"])

            # Add to local data
            local_data.append(
                {
                    "id": snapshot_id,
                    "block": block,
                    "timestamp": timestamp,
                    "embedding": emb,
                    "entropy": ent,
                    "perplexity": pp,
                }
            )

        self._save_local(local_data)

    async def run(self) -> None:
        """Run snapshot collection loop (once per day)."""
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - logging only
                logger.exception("State snapshot run failed")

            # Sleep until next day
            now = datetime.datetime.now()
            next_day = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            await asyncio.sleep(max(1, (next_day - now).total_seconds()))
