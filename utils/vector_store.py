import os
import glob
import json
import hashlib
import time
import asyncio
from typing import Optional, Any

from utils.logging import get_logger

from pinecone import Pinecone, PineconeException
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_fixed

from .atomic_json import atomic_json_dump

VECTOR_META_PATH = "vector_store.meta.json"
EMBED_DIM = 1536  # For OpenAI ada-002

logger = get_logger(__name__)


def file_hash(fname: str) -> str:
    with open(fname, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def scan_files(path: str = "config/*.md") -> dict[str, str]:
    files: dict[str, str] = {}
    for fname in glob.glob(path):
        files[fname] = file_hash(fname)
    return files


def load_vector_meta() -> dict[str, str]:
    if os.path.isfile(VECTOR_META_PATH):
        with open(VECTOR_META_PATH, "r") as f:
            return json.load(f)
    return {}


def save_vector_meta(meta: dict[str, str]) -> None:
    atomic_json_dump(VECTOR_META_PATH, meta)


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
async def safe_embed(text: str, client: AsyncOpenAI) -> list[float]:
    return await get_embedding(text, client)


async def get_embedding(text: str, client: AsyncOpenAI) -> list[float]:
    res = await client.embeddings.create(
        model="text-embedding-ada-002",
        input=text,
    )
    return res.data[0].embedding


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


class VectorStore:
    """Manage file vectorization and semantic search via Pinecone."""

    def __init__(
        self,
        openai_client: Optional[AsyncOpenAI] = None,
        *,
        openai_api_key: Optional[str] = None,
        pinecone_api_key: Optional[str] = None,
        pinecone_index: Optional[str] = None,
        backend: Optional[Any] = None,
        client_cls: Any = Pinecone,
    ) -> None:
        self.openai_client = openai_client or AsyncOpenAI(api_key=openai_api_key)
        self._pinecone_api_key = pinecone_api_key or os.getenv("PINECONE_API_KEY")
        self._pinecone_index = pinecone_index or os.getenv("PINECONE_INDEX")
        self._client_cls = client_cls
        self._pc: Optional[Pinecone] = None
        self._index = backend  # backend can be swapped in tests
        self._search_enabled = True

    # ------------------------------------------------------------------
    # Lazy Pinecone initialization
    # ------------------------------------------------------------------
    def _init_pinecone(self) -> None:
        """Initialize Pinecone connection if it hasn't been already."""

        if self._index is not None:
            return

        if self._pc is not None:
            return

        if not self._pinecone_api_key or not self._pinecone_index:
            logger.warning("Pinecone API key or index not set; search disabled")
            self._search_enabled = False
            return

        try:
            self._pc = self._client_cls(api_key=self._pinecone_api_key)
            indexes = None
            for attempt in range(3):
                try:
                    indexes = self._pc.list_indexes()
                    break
                except Exception as e:  # pragma: no cover - logging
                    logger.warning(
                        "Failed to list Pinecone indexes: %s (attempt %d/3)",
                        e,
                        attempt + 1,
                    )
                    time.sleep(1)
            if indexes is None:
                raise RuntimeError("Could not list Pinecone indexes")

            if self._pinecone_index not in [x["name"] for x in indexes]:
                for attempt in range(3):
                    try:
                        self._pc.create_index(
                            name=self._pinecone_index,
                            dimension=EMBED_DIM,
                            metric="cosine",
                        )
                        break
                    except Exception as e:  # pragma: no cover - logging
                        logger.warning(
                            "Failed to create Pinecone index: %s (attempt %d/3)",
                            e,
                            attempt + 1,
                        )
                        time.sleep(1)
                else:
                    raise RuntimeError("Could not create Pinecone index")

            self._index = self._pc.Index(self._pinecone_index)
        except Exception as e:  # pragma: no cover - logging
            logger.error("Failed to initialize Pinecone: %s", e)
            self._pc = None
            self._index = None
            self._search_enabled = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def vectorize_all_files(
        self,
        *,
        force: bool = False,
        on_message: Optional[Any] = None,
    ) -> dict[str, list[str]]:
        """Vectorize all markdown files and upsert into the backend."""

        self._init_pinecone()
        if not self._search_enabled or self._index is None:
            logger.warning("Vector store backend unavailable; skipping vectorization")
            return {"upserted": [], "deleted": []}

        current = scan_files()
        previous = load_vector_meta()
        changed = [f for f in current if (force or current[f] != previous.get(f))]
        new = [f for f in current if f not in previous]
        removed = [f for f in previous if f not in current]

        upserted_ids: list[str] = []
        for fname in current:
            if fname not in changed and fname not in new and not force:
                continue
            try:
                with open(fname, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception as e:  # pragma: no cover - file access
                logger.warning("Failed to read %s: %s", fname, e)
                continue
            chunks = chunk_text(text)
            for idx, chunk in enumerate(chunks):
                meta_id = f"{fname}:{idx}"
                try:
                    emb = await safe_embed(chunk, self.openai_client)
                    await asyncio.to_thread(
                        self._index.upsert,
                        vectors=[
                            {
                                "id": meta_id,
                                "values": emb,
                                "metadata": {
                                    "file": fname,
                                    "chunk": idx,
                                    "hash": current[fname],
                                },
                            }
                        ],
                    )
                    upserted_ids.append(meta_id)
                except PineconeException as e:
                    if on_message:
                        await on_message(f"Pinecone error: {e}")
                    logger.warning("Pinecone error for %s: %s", meta_id, e)
                except Exception as e:  # pragma: no cover - logging
                    logger.error("Failed to upsert %s: %s", meta_id, e)

        deleted_ids: list[str] = []
        for fname in removed:
            for idx in range(50):
                meta_id = f"{fname}:{idx}"
                try:
                    await asyncio.to_thread(self._index.delete, ids=[meta_id])
                    deleted_ids.append(meta_id)
                except Exception:  # pragma: no cover - best effort
                    continue

        save_vector_meta(current)
        if on_message:
            await on_message(
                "Vectorization complete. Added/changed: {}".format(
                    ", ".join(changed + new) if (changed or new) else "-"
                )
                + "; removed: {}".format(
                    ", ".join(removed) if removed else "-"
                )
            )
        return {"upserted": upserted_ids, "deleted": deleted_ids}

    async def semantic_search(
        self, query: str, *, top_k: int = 5
    ) -> list[str]:
        """Perform semantic search over stored vectors."""

        self._init_pinecone()
        if not self._search_enabled or self._index is None:
            logger.warning("Vector store backend unavailable; semantic search disabled")
            return []

        try:
            emb = await safe_embed(query, self.openai_client)
        except Exception as e:  # pragma: no cover - logging
            logger.error("Embedding failed: %s", e)
            return []

        try:
            res = await asyncio.to_thread(
                self._index.query,
                vector=emb,
                top_k=top_k,
                include_metadata=True,
            )
        except Exception as e:  # pragma: no cover - logging
            logger.error("Query failed: %s", e)
            return []

        chunks: list[str] = []
        matches = getattr(res, "matches", [])
        for match in matches:
            metadata = match.get("metadata", {})
            fname = metadata.get("file")
            chunk_idx = metadata.get("chunk")
            try:
                with open(fname, "r", encoding="utf-8") as f:
                    all_chunks = chunk_text(f.read())
                    if chunk_idx is not None and chunk_idx < len(all_chunks):
                        chunk_text_ = all_chunks[chunk_idx]
                    else:
                        chunk_text_ = ""
            except Exception:  # pragma: no cover - file access
                chunk_text_ = ""
            if chunk_text_:
                chunks.append(chunk_text_)
        return chunks

