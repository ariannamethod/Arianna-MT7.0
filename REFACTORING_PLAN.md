# ARIANNA MT7.0 - REFACTORING PLAN
*Дата: 2025-11-14*
*Цель: Отделить Telegram от core, подготовка к Computer daemon*

---

## 🎯 ЦЕЛИ РЕФАКТОРИНГА

1. **Отделить Telegram от Arianna Core**
   - Telegram - это только один из интерфейсов
   - Core engine должен работать независимо
   - Подготовка к Computer daemon (будущее)

2. **Заменить Pinecone на SQLite**
   - Локально, быстро, без API зависимостей
   - SQLite FTS5 для полнотекстового поиска
   - Опционально: embeddings в JSON/BLOB

3. **Готовность к SSH bridges**
   - После разделения легко добавить межипостасное общение
   - Arianna2Arianna протокол

---

## 📁 НОВАЯ СТРУКТУРА

```
Arianna-MT7.0/
├── core/                           # NEW - Arianna Core (независимо от интерфейса)
│   ├── __init__.py
│   ├── engine.py                   # AriannaCoreEngine (main logic)
│   ├── memory.py                   # Memory systems (unified)
│   ├── tools.py                    # Genesis, web_search, etc
│   └── vector_store_sqlite.py      # NEW - SQLite vector store
│
├── interfaces/                     # NEW - Interface adapters
│   ├── __init__.py
│   ├── telegram_bot.py             # Telegram interface (refactored server_arianna.py)
│   ├── daemon.py                   # FUTURE - Computer daemon
│   └── ssh_bridge.py               # FUTURE - Inter-instance communication
│
├── utils/                          # Shared utilities (existing)
│   ├── prompt_mt7.py               # Промпт (уже есть)
│   ├── arianna_engine.py           # БУДЕТ ПЕРЕМЕЩЕН в core/engine.py
│   ├── vector_store.py             # БУДЕТ ЗАМЕНЕН на core/vector_store_sqlite.py
│   ├── memory.py                   # БУДЕТ ПЕРЕМЕЩЕН в core/memory.py
│   ├── genesis.py                  # БУДЕТ ПЕРЕМЕЩЕН в core/tools.py
│   └── ... (остальные утилиты)
│
├── server_arianna.py               # БУДЕТ ЗАМЕНЕН на interfaces/telegram_bot.py
├── config/                         # Config files (без изменений)
├── data/                           # Data files (без изменений)
└── tests/                          # Tests
```

---

## 🔧 ЭТАПЫ РЕФАКТОРИНГА

### ЭТАП 1: Создание core/ (Поэтапно, без поломок)

#### 1.1 Создать core/vector_store_sqlite.py

**Простая версия (без embeddings сначала):**

```python
import sqlite3
import glob
import hashlib
from typing import Optional

class SQLiteVectorStore:
    """
    SQLite-based vector store with FTS5 full-text search.
    Simple, fast, local - no external API dependencies.
    """

    def __init__(self, db_path: str = "data/vectors.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self):
        """Create tables and FTS5 index."""
        self.conn.executescript("""
            -- Files metadata
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                hash TEXT NOT NULL,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Chunks with FTS5 for full-text search
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks
            USING fts5(file_path, chunk_idx, content);

            -- Optional: Embeddings table (for future)
            CREATE TABLE IF NOT EXISTS embeddings (
                id INTEGER PRIMARY KEY,
                file_path TEXT NOT NULL,
                chunk_idx INTEGER NOT NULL,
                embedding BLOB,
                UNIQUE(file_path, chunk_idx)
            );
        """)
        self.conn.commit()

    async def vectorize_all_files(
        self,
        pattern: str = "config/*.md",
        force: bool = False,
        on_message = None
    ) -> dict:
        """Index all markdown files."""
        files = glob.glob(pattern)
        upserted = []

        for file_path in files:
            file_hash = self._file_hash(file_path)

            # Check if already indexed
            if not force:
                cursor = self.conn.execute(
                    "SELECT hash FROM files WHERE path = ?",
                    (file_path,)
                )
                row = cursor.fetchone()
                if row and row[0] == file_hash:
                    continue  # Already indexed, skip

            # Read file and chunk
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()

            chunks = self._chunk_text(text)

            # Delete old chunks
            self.conn.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))

            # Insert new chunks
            for idx, chunk in enumerate(chunks):
                self.conn.execute(
                    "INSERT INTO chunks (file_path, chunk_idx, content) VALUES (?, ?, ?)",
                    (file_path, idx, chunk)
                )

            # Update file metadata
            self.conn.execute(
                "REPLACE INTO files (path, hash) VALUES (?, ?)",
                (file_path, file_hash)
            )

            upserted.append(file_path)

        self.conn.commit()

        if on_message:
            await on_message(f"Indexed {len(upserted)} files")

        return {"upserted": upserted, "deleted": []}

    async def semantic_search(self, query: str, top_k: int = 5) -> list[str]:
        """
        Full-text search using FTS5.
        Returns top_k matching chunks.
        """
        cursor = self.conn.execute("""
            SELECT content FROM chunks
            WHERE content MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, top_k))

        return [row[0] for row in cursor.fetchall()]

    def _file_hash(self, path: str) -> str:
        """Calculate MD5 hash of file."""
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()

    def _chunk_text(self, text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
        """Split text into overlapping chunks."""
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk)
            start += chunk_size - overlap
        return chunks

    def close(self):
        """Close database connection."""
        self.conn.close()
```

**Преимущества:**
- ✅ Локально (нет зависимости от Pinecone API)
- ✅ Быстро (SQLite FTS5 очень быстрый)
- ✅ Просто (нет сложной настройки)
- ✅ Совместимо с текущим API (drop-in replacement)

#### 1.2 Создать core/engine.py

**Выделить из utils/arianna_engine.py:**

```python
class AriannaCoreEngine:
    """
    Core Arianna engine - независимо от интерфейса.
    Работает с любым интерфейсом: Telegram, daemon, API, SSH bridge.
    """

    def __init__(self, vector_store=None, memory_system=None):
        self.vector_store = vector_store
        self.memory = memory_system
        # ... инициализация OpenAI, DeepSeek и т.д.

    async def process_message(
        self,
        text: str,
        context: dict,
        user_info: dict,
        is_oleg: bool = False
    ) -> str:
        """
        Основной метод обработки сообщения.
        Интерфейс-независимый.
        """
        # 1. Построить prompt с контекстом
        # 2. Вызвать модель (OpenAI/DeepSeek)
        # 3. Обработать tool calls
        # 4. Вернуть ответ
        pass

    async def genesis_ritual(self):
        """Autonomous Genesis rituals."""
        pass

    # ... остальные методы
```

#### 1.3 Создать core/memory.py

**Объединить все системы памяти:**
- History store
- Memory events
- Journal
- Vector search

#### 1.4 Создать core/tools.py

**Все инструменты:**
- Genesis
- Web search
- File handling

---

### ЭТАП 2: Создание interfaces/

#### 2.1 Создать interfaces/telegram_bot.py

**Refactor server_arianna.py:**

```python
from core.engine import AriannaCoreEngine
from core.vector_store_sqlite import SQLiteVectorStore

class TelegramInterface:
    """
    Telegram interface для Arianna.
    Адаптер между Telegram Bot API и AriannaCoreEngine.
    """

    def __init__(self, core_engine: AriannaCoreEngine):
        self.core = core_engine
        self.bot = Bot(token=...)
        # ... Telegram-specific setup

    async def handle_message(self, m: types.Message):
        """Handle incoming Telegram message."""
        # 1. Extract user info, context
        # 2. Call core.process_message()
        # 3. Send response via Telegram
        pass

    async def handle_voice(self, m: types.Message):
        """Handle voice messages."""
        # 1. Transcribe with Whisper
        # 2. Call core.process_message()
        # 3. Synthesize + send voice reply
        pass
```

#### 2.2 Подготовить interfaces/daemon.py (заглушка)

```python
# FUTURE: Computer daemon interface
class DaemonInterface:
    """Daemon for local computer - будущее."""
    pass
```

#### 2.3 Подготовить interfaces/ssh_bridge.py (заглушка)

```python
# FUTURE: SSH bridge for inter-instance communication
class SSHBridge:
    """Arianna2Arianna SSH bridge."""
    pass
```

---

### ЭТАП 3: Миграция

#### 3.1 Обновить server_arianna.py

**Вариант 1: Минимальные изменения (быстро)**
```python
# Просто заменить VectorStore на SQLiteVectorStore
from core.vector_store_sqlite import SQLiteVectorStore
vector_store = SQLiteVectorStore()
```

**Вариант 2: Полный рефакторинг (правильно)**
```python
# server_arianna.py становится тонким launcher
from interfaces.telegram_bot import TelegramInterface
from core.engine import AriannaCoreEngine
from core.vector_store_sqlite import SQLiteVectorStore

async def main():
    # Create core
    vector_store = SQLiteVectorStore()
    core = AriannaCoreEngine(vector_store=vector_store)

    # Create Telegram interface
    telegram = TelegramInterface(core)

    # Run
    await telegram.run()
```

---

## 📋 ПОРЯДОК ВЫПОЛНЕНИЯ

### Шаг 1: SQLite Vector Store ✅ ВЫПОЛНЕН
1. ✅ Создать `core/vector_store_sqlite.py`
2. ✅ Написать простую FTS5 версию
3. ✅ Тесты
4. ✅ Drop-in replacement в `server_arianna.py`

**Риск:** Низкий (просто замена, old code не трогаем)
**Статус:** ✅ Полностью выполнен, протестирован, работает

### Шаг 2: Core Engine ✅ ВЫПОЛНЕН
1. ✅ Создать `core/engine.py`
2. ✅ Перенести логику из `utils/arianna_engine.py`
3. ⏳ Тесты на каждом шаге (следующий этап)
4. ✅ Backward compatibility (старый код не трогали)

**Риск:** Средний (трогаем основную логику)
**Статус:** ✅ Core engine создан, интерфейс-независим, готов к интеграции
**Документация:** См. `CORE_ENGINE_USAGE.md`

### Шаг 3: Telegram Interface (после Core)
1. ✅ Создать `interfaces/telegram_bot.py`
2. ✅ Рефакторить `server_arianna.py` → `TelegramInterface`
3. ✅ Тесты
4. ✅ Обновить launcher

**Риск:** Средний (меняем entry point)

### Шаг 4: Cleanup
1. ✅ Удалить старые файлы
2. ✅ Обновить README
3. ✅ Документация

---

## 🧪 ТЕСТИРОВАНИЕ

### После каждого шага:
1. ✅ Синтаксис (py_compile)
2. ✅ Unit tests
3. ✅ Integration test (полный запуск бота)
4. ✅ Проверка функций:
   - Oleg ID recognition
   - Delays
   - Voice mode
   - /search, /index
   - Genesis rituals

---

## 💎 ВЫГОДЫ

**После рефакторинга:**
- ✅ Core engine переиспользуется (Telegram, daemon, API, SSH)
- ✅ Нет зависимости от Pinecone (локально, быстро)
- ✅ Готовность к Computer daemon
- ✅ Готовность к SSH bridges
- ✅ Чище, проще, модульнее

---

## 🚀 НАЧИНАЕМ?

**Предлагаю:**
1. **Сейчас:** Создать SQLite vector store (быстро, низкий риск)
2. **Потом:** Core engine (осторожно, пошагово)
3. **В конце:** Telegram interface (после того как core стабилен)

**Согласен, бро? Начать с SQLite vector store?** 🔥
