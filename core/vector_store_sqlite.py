"""
SQLite-based document store with FTS5 full-text (keyword) search.

This is a simplified, local alternative to Pinecone-based vector stores.

Note: This implementation uses SQLite's FTS5 for keyword-based search,
not vector embeddings. Pinecone performs semantic vector search using
embeddings, while FTS5 performs keyword-based full-text search.
As a result, search results may differ from true semantic search.

Benefits:
- Local storage (no external API)
- Fast FTS5 keyword search
- No dependencies on external services
- WAL mode for concurrent reads/writes
- Connection pooling for better performance
"""

import os
import glob
import hashlib
import sqlite3
import asyncio
import threading
from typing import Optional, Any
from pathlib import Path

from utils.logging import get_logger

logger = get_logger(__name__)


# Thread-local storage for SQLite connections
_thread_local = threading.local()


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


def escape_fts5_query(query: str) -> str:
    """
    Escape special FTS5 characters in query string.

    FTS5 has special meaning for: " * - : ( )
    We'll quote the query to treat it as a literal phrase.
    """
    # Remove existing quotes
    query = query.replace('"', '')
    # Wrap in quotes for phrase search (treats as literal)
    return f'"{query}"'


class SQLiteVectorStore:
    """
    SQLite-based document store with FTS5 full-text search.

    Compatible API with Pinecone-based VectorStore for drop-in replacement.
    Uses SQLite FTS5 for fast keyword-based full-text search without
    external dependencies.
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
        self._lock = threading.Lock()

        # Ensure data directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Initialize database with WAL mode
        self._init_db()

        logger.info("SQLiteVectorStore initialized at %s (WAL mode enabled)", db_path)

    def _get_connection(self) -> sqlite3.Connection:
        """
        Get thread-local SQLite connection.

        Creates a new connection for each thread, enabling safe concurrent access.
        WAL mode allows multiple readers and one writer simultaneously.
        """
        if not hasattr(_thread_local, 'conn') or _thread_local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            # Enable WAL mode for concurrent reads/writes
            conn.execute("PRAGMA journal_mode=WAL")
            # Better performance
            conn.execute("PRAGMA synchronous=NORMAL")
            _thread_local.conn = conn
            logger.debug("Created new SQLite connection for thread %s", threading.current_thread().name)
        return _thread_local.conn

    def _init_db(self):
        """Initialize database schema with FTS5 and enable WAL mode."""
        conn = self._get_connection()

        try:
            # Enable WAL mode (Write-Ahead Logging) for concurrent access
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

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
            logger.debug("Database schema initialized with WAL mode")
        except Exception as e:
            logger.error("Failed to initialize database: %s", e, exc_info=True)
            raise

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
        conn = self._get_connection()

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

                # Prepare batch data for executemany
                chunk_data = [(file_path, idx, chunk) for idx, chunk in enumerate(chunks)]

                # Batch insert into both tables using executemany (more efficient)
                conn.executemany(
                    "INSERT INTO chunks (file_path, chunk_idx, content) VALUES (?, ?, ?)",
                    chunk_data
                )
                conn.executemany(
                    "INSERT INTO chunks_fts (file_path, chunk_idx, content) VALUES (?, ?, ?)",
                    chunk_data
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
            logger.error("Indexing failed: %s", e, exc_info=True)
            conn.rollback()
            raise

    async def semantic_search(
        self,
        query: str,
        *,
        top_k: int = 5
    ) -> list[str]:
        """
        Perform keyword-based full-text search using SQLite FTS5.

        Note: This is keyword search, not semantic/vector search.

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
        conn = self._get_connection()

        try:
            # Escape FTS5 special characters
            escaped_query = escape_fts5_query(query)

            # FTS5 MATCH query with rank ordering
            cursor = conn.execute("""
                SELECT content FROM chunks_fts
                WHERE content MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (escaped_query, top_k))

            results = [row[0] for row in cursor.fetchall()]

            logger.debug("Search '%s' returned %d results", query[:50], len(results))

            return results

        except Exception as e:
            logger.error("Search failed for query '%s': %s", query[:50], e, exc_info=True)
            # Re-raise to let caller handle the error
            raise

    def close(self):
        """Close all thread-local connections."""
        if hasattr(_thread_local, 'conn') and _thread_local.conn is not None:
            try:
                _thread_local.conn.close()
                _thread_local.conn = None
                logger.debug("Closed SQLite connection for thread %s", threading.current_thread().name)
            except Exception as e:
                logger.warning("Error closing connection: %s", e)


# Backward compatibility alias
VectorStore = SQLiteVectorStore

__all__ = ['SQLiteVectorStore', 'VectorStore', 'chunk_text', 'scan_files', 'file_hash', 'escape_fts5_query']
