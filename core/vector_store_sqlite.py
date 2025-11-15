"""
SQLite-based vector store with FTS5 full-text search.

Drop-in replacement for Pinecone-based vector store.
Benefits:
- Local storage (no external API)
- Fast FTS5 search
- No dependencies on external services
- Simple setup
"""

import os
import glob
import hashlib
import sqlite3
import asyncio
from typing import Optional, Any
from pathlib import Path

from utils.logging import get_logger

logger = get_logger(__name__)


def file_hash(fname: str) -> str:
    """Calculate MD5 hash of file."""
    with open(fname, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def scan_files(pattern: str = "config/*.md") -> dict[str, str]:
    """Scan files matching pattern and return path -> hash mapping."""
    files: dict[str, str] = {}
    for fname in glob.glob(pattern):
        try:
            files[fname] = file_hash(fname)
        except Exception as e:
            logger.warning("Failed to hash %s: %s", fname, e)
    return files


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    """Split text into overlapping chunks."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


class SQLiteVectorStore:
    """
    SQLite-based vector store with FTS5 full-text search.

    Compatible API with Pinecone-based VectorStore for drop-in replacement.
    Uses SQLite FTS5 for fast full-text search without external dependencies.
    """

    def __init__(
        self,
        db_path: str = "data/vectors.db",
        openai_client: Optional[Any] = None,
    ):
        """
        Initialize SQLite vector store.

        Parameters
        ----------
        db_path : str
            Path to SQLite database file
        openai_client : Optional[Any]
            OpenAI client (for API compatibility, not used in FTS5 mode)
        """
        self.db_path = db_path
        self.openai_client = openai_client

        # Ensure data directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_db()

        logger.info("SQLiteVectorStore initialized at %s", db_path)

    def _init_db(self):
        """Initialize database schema with FTS5."""
        conn = sqlite3.connect(self.db_path)

        try:
            conn.executescript("""
                -- Files metadata table
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    hash TEXT NOT NULL,
                    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- FTS5 virtual table for full-text search
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(
                    file_path UNINDEXED,
                    chunk_idx UNINDEXED,
                    content,
                    tokenize='porter unicode61'
                );

                -- Metadata table for chunks (parallel to FTS5)
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    chunk_idx INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    UNIQUE(file_path, chunk_idx)
                );
            """)
            conn.commit()
            logger.debug("Database schema initialized")
        except Exception as e:
            logger.error("Failed to initialize database: %s", e)
            raise
        finally:
            conn.close()

    async def vectorize_all_files(
        self,
        *,
        force: bool = False,
        on_message: Optional[Any] = None,
        pattern: str = "config/*.md",
    ) -> dict[str, list[str]]:
        """
        Index all markdown files into SQLite FTS5.

        Parameters
        ----------
        force : bool
            Force reindexing even if files haven't changed
        on_message : Optional[callable]
            Callback for progress messages
        pattern : str
            Glob pattern for files to index

        Returns
        -------
        dict
            {"upserted": [file_paths], "deleted": [file_paths]}
        """
        return await asyncio.to_thread(
            self._vectorize_all_files_sync,
            force=force,
            on_message=on_message,
            pattern=pattern
        )

    def _vectorize_all_files_sync(
        self,
        force: bool,
        on_message: Optional[Any],
        pattern: str,
    ) -> dict[str, list[str]]:
        """Synchronous implementation of vectorize_all_files."""
        conn = sqlite3.connect(self.db_path)

        try:
            current = scan_files(pattern)

            # Get previously indexed files
            cursor = conn.execute("SELECT path, hash FROM files")
            previous = {row[0]: row[1] for row in cursor.fetchall()}

            # Determine which files need indexing
            changed = [f for f in current if (force or current[f] != previous.get(f))]
            new = [f for f in current if f not in previous]
            removed = [f for f in previous if f not in current]

            upserted_files: list[str] = []

            # Index changed/new files
            for file_path in current:
                if file_path not in changed and file_path not in new and not force:
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        text = f.read()
                except Exception as e:
                    logger.warning("Failed to read %s: %s", file_path, e)
                    continue

                chunks = chunk_text(text)

                # Delete old chunks for this file
                conn.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))
                conn.execute("DELETE FROM chunks_fts WHERE file_path = ?", (file_path,))

                # Insert new chunks
                for idx, chunk in enumerate(chunks):
                    # Insert into metadata table
                    conn.execute(
                        "INSERT INTO chunks (file_path, chunk_idx, content) VALUES (?, ?, ?)",
                        (file_path, idx, chunk)
                    )

                    # Insert into FTS5 table
                    conn.execute(
                        "INSERT INTO chunks_fts (file_path, chunk_idx, content) VALUES (?, ?, ?)",
                        (file_path, idx, chunk)
                    )

                # Update file metadata
                conn.execute(
                    "REPLACE INTO files (path, hash) VALUES (?, ?)",
                    (file_path, current[file_path])
                )

                upserted_files.append(file_path)
                logger.debug("Indexed %s (%d chunks)", file_path, len(chunks))

            # Remove deleted files
            deleted_files: list[str] = []
            for file_path in removed:
                conn.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))
                conn.execute("DELETE FROM chunks_fts WHERE file_path = ?", (file_path,))
                conn.execute("DELETE FROM files WHERE path = ?", (file_path,))
                deleted_files.append(file_path)
                logger.debug("Removed %s from index", file_path)

            conn.commit()

            logger.info(
                "Indexing complete: %d upserted, %d deleted",
                len(upserted_files),
                len(deleted_files)
            )

            return {
                "upserted": upserted_files,
                "deleted": deleted_files,
            }

        except Exception as e:
            logger.error("Indexing failed: %s", e)
            conn.rollback()
            raise
        finally:
            conn.close()

    async def semantic_search(
        self,
        query: str,
        *,
        top_k: int = 5
    ) -> list[str]:
        """
        Perform full-text search using SQLite FTS5.

        Parameters
        ----------
        query : str
            Search query
        top_k : int
            Number of results to return

        Returns
        -------
        list[str]
            List of matching text chunks
        """
        return await asyncio.to_thread(
            self._semantic_search_sync,
            query=query,
            top_k=top_k
        )

    def _semantic_search_sync(self, query: str, top_k: int) -> list[str]:
        """Synchronous implementation of semantic_search."""
        conn = sqlite3.connect(self.db_path)

        try:
            # FTS5 MATCH query with rank ordering
            cursor = conn.execute("""
                SELECT content FROM chunks_fts
                WHERE content MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, top_k))

            results = [row[0] for row in cursor.fetchall()]

            logger.debug("Search '%s' returned %d results", query[:50], len(results))

            return results

        except Exception as e:
            logger.error("Search failed: %s", e)
            return []
        finally:
            conn.close()

    def close(self):
        """Close database (no-op for connection-per-query model)."""
        pass


# Backward compatibility alias
VectorStore = SQLiteVectorStore

__all__ = ['SQLiteVectorStore', 'VectorStore', 'chunk_text', 'scan_files', 'file_hash']
