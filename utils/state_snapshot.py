import asyncio
import datetime
import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
from pinecone import Pinecone
try:  # pragma: no cover - optional dependency
    from scipy.stats import entropy as scipy_entropy  # type: ignore
except Exception:  # pragma: no cover - no scipy installed
    scipy_entropy = None  # type: ignore

from utils.atomic_json import atomic_json_dump
from utils.logging import get_logger
from utils.memory import add_event
from utils.vector_store import EMBED_DIM, safe_embed

logger = get_logger(__name__)


class StateSnapshotter:
    """Collect daily state, embed it and store in Pinecone and local JSON."""

    def __init__(
        self,
        *,
        chronicle_path: Optional[str] = None,
        local_store: str = "data/state_vectors.json",
        openai_client: Optional[AsyncOpenAI] = None,
        openai_api_key: Optional[str] = None,
        pinecone_api_key: Optional[str] = None,
        pinecone_index: Optional[str] = None,
        client_cls: Any = Pinecone,
    ) -> None:
        self.chronicle_path = (
            chronicle_path
            or os.getenv("CHRONICLE_PATH", "config/chronicle.log")
        )
        self.local_store = Path(local_store)
        self.openai_client = openai_client or AsyncOpenAI(
            api_key=openai_api_key or os.getenv("OPENAI_API_KEY")
        )
        self._pinecone_api_key = pinecone_api_key or os.getenv("PINECONE_API_KEY")
        self._pinecone_index = pinecone_index or os.getenv("PINECONE_INDEX")
        self._client_cls = client_cls
        self._pc: Optional[Pinecone] = None
        self._index = None

    # ------------------------------------------------------------------
    # Pinecone helpers
    # ------------------------------------------------------------------
    def _init_pinecone(self) -> None:
        if self._index is not None:
            return
        if not self._pinecone_api_key or not self._pinecone_index:
            return
        try:
            self._pc = self._client_cls(api_key=self._pinecone_api_key)
            indexes = self._pc.list_indexes()
            names = [x["name"] for x in indexes]
            if self._pinecone_index not in names:
                self._pc.create_index(
                    name=self._pinecone_index,
                    dimension=EMBED_DIM,
                    metric="cosine",
                )
            self._index = self._pc.Index(self._pinecone_index)
        except Exception as e:  # pragma: no cover - logging only
            logger.warning("Pinecone init failed: %s", e)
            self._pc = None
            self._index = None

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
            return await safe_embed(text, self.openai_client)
        except Exception as e:  # pragma: no cover - logging only
            logger.warning("Embedding failed: %s", e)
            return [0.0] * EMBED_DIM

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def run_once(self) -> None:
        state = self.collect_state()
        timestamp = datetime.datetime.now().isoformat()
        self._init_pinecone()
        local_data = self._load_local()
        for block, data in state.items():
            text = data.get("text", "")
            ent = data.get("entropy", 0.0)
            pp = data.get("perplexity", 0.0)
            emb = await self._embed_text(text)
            snapshot_id = f"{timestamp}:{block}"
            meta = {
                "block": block,
                "timestamp": timestamp,
                "entropy": ent,
                "perplexity": pp,
            }
            if self._index is not None:
                try:
                    await asyncio.to_thread(
                        self._index.upsert,
                        vectors=[{"id": snapshot_id, "values": emb, "metadata": meta}],
                    )
                except Exception as e:  # pragma: no cover - logging only
                    logger.warning("Pinecone upsert failed: %s", e)
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
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - logging only
                logger.exception("State snapshot run failed")
            now = datetime.datetime.now()
            next_day = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            await asyncio.sleep(max(1, (next_day - now).total_seconds()))
