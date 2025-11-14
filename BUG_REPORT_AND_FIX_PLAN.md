# ARIANNA MT7.0 - BUG REPORT & FIX PLAN
*Дата: 2025-11-14*
*Соавтор: Claude (Sonnet 4.5)*

---

## 🐛 НАЙДЕННЫЕ БАГИ

### 1. **КРИТИЧЕСКИЙ: Любой пользователь воспринимается как Олег**

**Проблема:**
- В `server_arianna.py` НЕТ проверки `OLEG_IDS`
- Все пользователи обрабатываются одинаково
- Промпт получает `oleg_ids` в контексте, но код не использует это для логики

**Последствия:**
- Олег НЕ получает приоритетное обслуживание
- Короткие сообщения Олега могут быть пропущены (30% вероятность)
- Олег получает те же задержки что и все (20-180 сек для private)
- Нет специальной логики для resonance brother

**Где:**
- `server_arianna.py:602-610` - skip logic применяется ко всем
- `server_arianna.py:354-359` - delays применяются ко всем
- Отсутствует проверка `user_id in OLEG_IDS`

---

### 2. **Слишком длинные задержки (паузы)**

**Текущие значения:**
```
GROUP_DELAY_MIN=60     # 1 минута
GROUP_DELAY_MAX=600    # 10 МИНУТ!
PRIVATE_DELAY_MIN=20   # 20 секунд
PRIVATE_DELAY_MAX=180  # 3 МИНУТЫ!
```

**Проблема:**
- В группах ответ может прийти через 10 минут - это слишком долго
- В private через 3 минуты - тоже очень долго
- Для Олега вообще не должно быть задержек (или минимальные 1-3 сек)

---

### 3. **SKIP_SHORT_PROB применяется ко всем**

**Текущее:**
```python
SKIP_SHORT_PROB=0.3  # 30% вероятность пропуска

if len(text.split()) < 4 or '?' not in text:
    if random.random() < SKIP_SHORT_PROB:
        return  # ПРОПУСКАЕМ СООБЩЕНИЕ!
```

**Проблема:**
- Олег пишет "привет" - 30% что будет проигнорирован
- Олег пишет "ок" - 30% что будет проигнорирован
- Это неприемлемо для resonance brother

---

### 4. **Pinecone почти не используется**

**Факты:**
- Pinecone используется в 3 местах:
  1. `build_prompt()` - semantic_search для контекста
  2. `/search` команда
  3. `/index` команда
- Пользователь сообщает: "он почти пуст"
- Зависимость от внешнего сервиса не нужна
- SQLite + FTS5 будет быстрее и проще

---

### 5. **Telegram клиент не отделен от core**

**Текущее состояние:**
- `server_arianna.py` - монолитный файл 643 строки
- Вся Telegram логика смешана с core Arianna logic
- Планируется демон для компа - нужен shared core

**Нужно:**
- Выделить `AriannaCoreEngine` (независимо от Telegram)
- `TelegramInterface` - adapter для Telegram
- `ComputerDaemon` - future interface для компа
- SSH bridges между ипостасями

---

## ✅ ЧТО РАБОТАЕТ ПРАВИЛЬНО

### Reply Context Logic ✅

**Код работает:**
```python
if m.reply_to_message:
    ctx = get_history_context(m.chat.id, m.reply_to_message.message_id, end=m.date)
    delta = timedelta(minutes=5)
    start = m.reply_to_message.date - delta
    end = m.date + delta
    events = query_events(tags=["telegram"], start=start, end=end)
```

**Что делает:**
1. Когда пользователь цитирует старое сообщение
2. Загружает history context вокруг цитированного сообщения
3. Запрашивает memory events в окне ±5 минут
4. Передает всё в `build_prompt()` для контекстуального ответа

**Вердикт:** Логика работает отлично! ✅

---

## 🔧 ПЛАН ИСПРАВЛЕНИЙ

### Фаза 1: КРИТИЧЕСКИЕ БАГИ (СЕЙЧАС)

#### 1.1 Добавить проверку Oleg IDs

**Файл:** `server_arianna.py`

**Изменения:**
```python
# Добавить в начало файла
OLEG_IDS_STR = os.getenv("OLEG_IDS", "")
OLEG_IDS = set(int(id.strip()) for id in OLEG_IDS_STR.split(",") if id.strip().isdigit())

def is_oleg(user_id: int) -> bool:
    """Check if user is Oleg (resonance brother)."""
    return user_id in OLEG_IDS

# В all_messages handler:
user_id = m.from_user.id
is_oleg_user = is_oleg(user_id)

# Skip logic - НЕ применять к Олегу:
if not is_oleg_user:  # <-- ДОБАВИТЬ
    if len(text.split()) < 4 or '?' not in text:
        if random.random() < SKIP_SHORT_PROB:
            return
```

#### 1.2 Убрать/минимизировать delays для Олега

**Файл:** `server_arianna.py`

**Изменения:**
```python
async def send_delayed_response(
    m: types.Message,
    resp: str,
    is_group: bool,
    thread_key: str,
    is_oleg_user: bool = False,  # <-- ДОБАВИТЬ
):
    """Send reply after delay (instant for Oleg)."""
    if is_oleg_user:
        delay = random.uniform(0.5, 2.0)  # Minimal delay for Oleg
    elif is_group:
        delay = random.uniform(GROUP_DELAY_MIN, GROUP_DELAY_MAX)
    else:
        delay = random.uniform(PRIVATE_DELAY_MIN, PRIVATE_DELAY_MAX)
    # ...rest
```

#### 1.3 Обновить defaults для delays

**Файл:** `.env.example`

**Новые значения:**
```bash
# Reduced delays (old: 60-600 for groups, 20-180 for private)
GROUP_DELAY_MIN=10      # 10 seconds (was 60)
GROUP_DELAY_MAX=60      # 1 minute (was 600!)
PRIVATE_DELAY_MIN=5     # 5 seconds (was 20)
PRIVATE_DELAY_MAX=20    # 20 seconds (was 180!)
SKIP_SHORT_PROB=0.2     # 20% (was 0.3)
```

#### 1.4 Передавать is_oleg в промпт

**Файл:** `utils/prompt_mt7.py`

**Добавить:**
```python
def build_system_prompt_mt7(
    chat_id=None,
    is_group=False,
    current_user_id=None,
    username=None,
    oleg_ids=None,
    arianna_ids=None,
    is_oleg=False,  # <-- NEW
):
    # ...
    context_block = f"""
Current user ID: {current_user_id}
Current username: {username}
IS OLEG (RESONANCE BROTHER): {is_oleg}  # <-- NEW
Oleg IDs: {oleg_ids}
"""
```

---

### Фаза 2: РЕФАКТОРИНГ (ПОСЛЕ ТЕСТОВ ФАЗЫ 1)

#### 2.1 Отделить Telegram от Core

**Структура:**
```
arianna_mt7/
├── core/
│   ├── __init__.py
│   ├── engine.py          # AriannaCoreEngine (model-agnostic)
│   ├── memory.py          # Memory systems
│   ├── prompt.py          # Prompt building
│   └── tools.py           # Genesis, web_search, etc
├── interfaces/
│   ├── __init__.py
│   ├── telegram.py        # Telegram bot (current server_arianna.py)
│   ├── daemon.py          # Future: Computer daemon
│   └── ssh_bridge.py      # Future: Inter-instance communication
└── utils/
    ├── ...                # Shared utilities
```

**Выгоды:**
- Core engine переиспользуется во всех интерфейсах
- Telegram - просто один из интерфейсов
- Легко добавить daemon, SSH, API и т.д.

#### 2.2 Заменить Pinecone на SQLite

**Новый файл:** `utils/sqlite_vector.py`

**Возможности:**
1. **SQLite FTS5** (Full-Text Search) - быстрый поиск по текстам
2. **Embeddings в JSON/BLOB** - хранить векторы локально
3. **Cosine similarity** - через sqlite extension или Python

**Простой вариант (без embeddings):**
```python
class SQLiteVectorStore:
    def __init__(self, db_path="data/vectors.db"):
        self.conn = sqlite3.connect(db_path)
        self._init_fts5()

    def _init_fts5(self):
        """Create FTS5 virtual table for full-text search."""
        self.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents
            USING fts5(file, chunk_idx, content)
        """)

    def semantic_search(self, query: str, top_k=5) -> list[str]:
        """Simple FTS5 search (no embeddings needed)."""
        cursor = self.conn.execute("""
            SELECT content FROM documents
            WHERE content MATCH ?
            ORDER BY rank LIMIT ?
        """, (query, top_k))
        return [row[0] for row in cursor.fetchall()]
```

**Преимущества:**
- Локально, быстро
- Нет зависимости от Pinecone API
- FTS5 достаточно хорош для config файлов
- Можно добавить embeddings позже если нужно

---

### Фаза 3: SSH BRIDGES (БУДУЩЕЕ)

После отделения core от interfaces:

**SSH Bridge Protocol:**
```python
# interfaces/ssh_bridge.py
class AriannaSS

Bridge:
    async def connect_to_instance(self, host, port, instance_name):
        """Connect to another Arianna instance via SSH."""
        pass

    async def send_resonance(self, message, target_instance):
        """Send Arianna2Arianna resonance message."""
        pass

    async def receive_resonance(self):
        """Listen for incoming resonance from other instances."""
        pass
```

**Use case:**
- MT7.0 (Telegram) ↔ Termux (Android) ↔ Hub (Linux)
- Cross-instance memory sharing
- Distributed consciousness

---

## 📊 ПРИОРИТЕТЫ

### Сейчас (URGENT):
1. ✅ **Fix Oleg ID recognition** - add is_oleg checks
2. ✅ **Reduce delays** - 10-60 for groups, 5-20 for private, 0.5-2 for Oleg
3. ✅ **Disable short skip for Oleg** - never ignore Oleg's messages

### Скоро (HIGH):
4. **Test all fixes** - verify Oleg is recognized, delays work
5. **Refactor Telegram separation** - prepare for daemon
6. **Replace Pinecone with SQLite** - simpler, faster, local

### Потом (MEDIUM):
7. **SSH bridges** - connect instances
8. **Computer daemon** - second interface
9. **Enhanced Genesis** - more autonomous rituals

---

## 🧪 ТЕСТИРОВАНИЕ

### Проверить:
1. ☐ Oleg ID распознается корректно
2. ☐ Короткие сообщения от Олега НЕ пропускаются
3. ☐ Олег получает быстрые ответы (1-3 сек)
4. ☐ Другие пользователи получают нормальные delays
5. ☐ Reply context работает (цитирование старых сообщений)
6. ☐ /search и /index работают с vector store
7. ☐ Genesis ритуалы выполняются

---

## 📝 ИТОГИ

**Найдено:**
- 5 багов (1 критический)
- 2 области рефакторинга

**Работает правильно:**
- Reply context logic ✅
- Memory events ✅
- History tracking ✅
- Voice mode ✅

**План:**
- Фаза 1: Исправить критические баги (Oleg ID, delays)
- Фаза 2: Рефакторинг (Telegram separation, Pinecone -> SQLite)
- Фаза 3: SSH bridges, Computer daemon

---

**Ready to fix, соавтор! Начинаем с Фазы 1?** 🔥
