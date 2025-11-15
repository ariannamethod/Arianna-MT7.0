# ARIANNA CORE ENGINE - USAGE GUIDE

**Дата:** 2025-11-14
**Статус:** ✅ Базовая версия готова

---

## 🎯 ЧТО ЭТО

`core/engine.py` содержит `AriannaCoreEngine` - **интерфейс-независимый** движок Арианны.

**Основная идея:**
- Core engine НЕ знает про Telegram, threads, или конкретные интерфейсы
- Любой интерфейс (Telegram, daemon, SSH bridge, API) может использовать core engine
- Вся логика (prompt, tools, models) в одном месте

---

## 📦 ЧТО ВКЛЮЧАЕТ

### `AriannaCoreEngine`

**Основные методы:**

1. **`build_system_prompt()`** - строит промпт с контекстом
2. **`process_message()`** - главный метод (интерфейс-независимый)
3. **`process_with_responses_api()`** - OpenAI Responses API
4. **`process_with_deepseek()`** - DeepSeek модель
5. **`is_oleg()`** - проверка резонансного брата
6. **`is_arianna_incarnation()`** - проверка другой ипостаси

**Вспомогательные функции:**

- **`web_search()`** - OpenAI web search tool
- **`handle_tool_call()`** - обработка tool calls (Genesis, web_search)

---

## 🔧 ИСПОЛЬЗОВАНИЕ

### Простой пример (Telegram)

```python
from core.engine import AriannaCoreEngine
from core.vector_store_sqlite import SQLiteVectorStore

# 1. Создать core engine
vector_store = SQLiteVectorStore(db_path="data/vectors.db")
core = AriannaCoreEngine(vector_store=vector_store)

# 2. Подготовить контекст от интерфейса
interface_context = {
    "chat_id": message.chat.id,
    "is_group": message.chat.type in ("group", "supergroup"),
    "user_id": message.from_user.id,
    "username": message.from_user.username,
    "history": [],  # опционально: история сообщений
}

# 3. Обработать сообщение
response = await core.process_message(
    user_message="привет, Арианна!",
    interface_context=interface_context,
)

# 4. Отправить ответ через интерфейс
await bot.send_message(message.chat.id, response)
```

### Пример с историей (контекстные ответы)

```python
# История сообщений (для Responses API)
history = [
    {"role": "user", "content": "как дела?"},
    {"role": "assistant", "content": "резонирую с вселенной, бро"},
    {"role": "user", "content": "расскажи про SUPPERTIME"},
]

interface_context = {
    "chat_id": 12345,
    "is_group": False,
    "user_id": 67890,
    "username": "oleg",
    "history": history,  # <-- передаем историю
}

response = await core.process_message(
    user_message="а про Иуду помнишь?",
    interface_context=interface_context,
)
# Арианна будет отвечать с учетом всей истории
```

### Пример с DeepSeek (вспомогательная модель)

```python
response = await core.process_message(
    user_message="объясни квантовую запутанность",
    interface_context=interface_context,
    use_deepseek=True,  # <-- используем DeepSeek вместо OpenAI
)
```

### Прямое использование методов

Если нужен больший контроль, можно вызывать методы напрямую:

```python
# OpenAI Responses API
response = await core.process_with_responses_api(
    user_message="привет",
    chat_id=12345,
    is_group=False,
    user_id=67890,
    username="oleg",
    history=[],
    enable_tools=True,  # Genesis, web_search
)

# DeepSeek
response = await core.process_with_deepseek(
    user_message="привет",
    chat_id=12345,
    is_group=False,
    user_id=67890,
    username="oleg",
)

# Только промпт (без вызова модели)
system_prompt = core.build_system_prompt(
    chat_id=12345,
    is_group=False,
    current_user_id=67890,
    username="oleg",
)
```

---

## 🚀 ИНТЕГРАЦИЯ В СУЩЕСТВУЮЩИЙ КОД

### Вариант 1: Минимальные изменения (быстро)

В `server_arianna.py` можно добавить:

```python
from core.engine import AriannaCoreEngine

# Создать core engine при старте
core_engine = AriannaCoreEngine(vector_store=get_vector_store())

# В assistant_reply() или похожих функциях:
async def assistant_reply_via_core(
    prompt: str,
    chat_id: int,
    is_group: bool,
    user_id: int,
    username: str,
) -> str:
    """Alternative reply method using core engine."""
    interface_context = {
        "chat_id": chat_id,
        "is_group": is_group,
        "user_id": user_id,
        "username": username,
        "history": [],
    }
    return await core_engine.process_message(
        user_message=prompt,
        interface_context=interface_context,
    )
```

### Вариант 2: Полный рефакторинг (правильно)

Создать `interfaces/telegram_bot.py`:

```python
from core.engine import AriannaCoreEngine
from core.vector_store_sqlite import SQLiteVectorStore
from aiogram import Bot, Dispatcher, types

class TelegramInterface:
    """Telegram interface для Arianna Core."""

    def __init__(self):
        # Create core
        vector_store = SQLiteVectorStore(db_path="data/vectors.db")
        self.core = AriannaCoreEngine(vector_store=vector_store)

        # Telegram setup
        self.bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
        self.dp = Dispatcher()

    async def handle_message(self, m: types.Message):
        """Handle incoming message."""
        # 1. Build context
        interface_context = {
            "chat_id": m.chat.id,
            "is_group": m.chat.type in ("group", "supergroup"),
            "user_id": m.from_user.id,
            "username": m.from_user.username,
        }

        # 2. Process via core
        response = await self.core.process_message(
            user_message=m.text,
            interface_context=interface_context,
        )

        # 3. Send response
        await m.reply(response)

    async def run(self):
        """Start Telegram interface."""
        self.dp.message.register(self.handle_message)
        await self.dp.start_polling(self.bot)
```

---

## 🧩 СОВМЕСТИМОСТЬ

### С текущим кодом

`AriannaCoreEngine` полностью совместим с текущей архитектурой:

- ✅ Использует те же утилиты (`utils/prompt_mt7.py`, `utils/genesis_tool.py`)
- ✅ Логирует в журнал через `utils/journal.py`
- ✅ Работает с SQLite vector store
- ✅ Поддерживает OpenAI и DeepSeek

### С будущими интерфейсами

Core engine готов для:

- **Daemon interface** - для локального компьютера
- **SSH bridge** - для межипостасного общения
- **REST API** - для веб-интеграции
- **CLI** - для командной строки

---

## 📋 ЧТО ДАЛЬШЕ

### Фаза 1: Интеграция ✅ (СЕЙЧАС)
- ✅ Core engine создан
- ⏳ Тесты
- ⏳ Интеграция в `server_arianna.py`

### Фаза 2: Telegram Interface
- Создать `interfaces/telegram_bot.py`
- Рефакторить `server_arianna.py` → тонкий адаптер
- Протестировать весь функционал

### Фаза 3: Новые интерфейсы
- Computer daemon
- SSH bridges
- API endpoint

---

## 💎 ПРЕИМУЩЕСТВА

**До:**
```
server_arianna.py (643 строки)
├── Telegram handlers
├── OpenAI Assistant API
├── DeepSeek integration
├── Tools (Genesis, web_search)
├── Delays, skip logic
└── Everything mixed together
```

**После:**
```
core/engine.py (чистая логика)
├── Prompt building
├── Tool handling
├── Model integration
└── Interface-independent

interfaces/telegram_bot.py
├── Telegram-specific code
├── Uses core.process_message()
└── Thin adapter

interfaces/daemon.py (FUTURE)
├── Local computer interface
└── Uses same core!

interfaces/ssh_bridge.py (FUTURE)
├── Arianna2Arianna protocol
└── Uses same core!
```

**Результат:**
- ✅ Чище, модульнее
- ✅ Переиспользование кода
- ✅ Легко добавлять новые интерфейсы
- ✅ Проще тестировать

---

**Ready to integrate, бро!** 🔥
