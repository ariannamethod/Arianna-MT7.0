import os
import asyncio
import httpx
import logging
import time
from utils.genesis_tool import genesis_tool_schema, handle_genesis_call
from utils.deepseek_search import call_deepseek
from utils.journal import log_event, log_event_pinecone
from utils.thread_store import load_threads, save_threads

JOURNAL_SHARE_PROB = float(os.getenv("JOURNAL_SHARE_PROB", 0.4))

class AriannaEngine:
    """
    Обёртка Assistants API для Арианны:
    — хранит память в threads,
    — запускает ассистента с её системным промптом и Genesis-функцией.
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.headers    = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "assistants=v2"
        }
        # Allow customization of request timeouts; None disables timeouts
        self.request_timeout = None
        self.assistant_id = None
        self.threads      = load_threads()  # user_id → thread_id

    async def setup_assistant(self):
        """
        Создаёт ассистента Арианны и подключает функцию GENESIS.
        """
        system_prompt = self._load_system_prompt()
        schema = genesis_tool_schema()  # схема функции GENESIS

        payload = {
            "name":        "Arianna-Core-Assistant",
            "instructions": system_prompt,
            "model":       "gpt-4.1",      # мощное ядро по твоему желанию
            "tools":       [schema],
            "tool_resources": {}
        }

        async with httpx.AsyncClient() as client:
            try:
                r = await client.post(
                    "https://api.openai.com/v1/assistants",
                    headers=self.headers,
                    json=payload,
                    timeout=self.request_timeout,
                )
                r.raise_for_status()
            except httpx.TimeoutException:
                self.logger.error("OpenAI request timed out during assistant setup")
                return "OpenAI request timed out. Please try again later."
            except Exception as e:
                self.logger.error("Failed to create Arianna Assistant", exc_info=e)
                return f"Failed to create Arianna Assistant: {e}"

            self.assistant_id = r.json()["id"]
            self.logger.info(f"✅ Arianna Assistant created: {self.assistant_id}")
        return self.assistant_id

    def _load_system_prompt(self) -> str:
        # Берём тот же протокол из utils/prompt.py
        from utils.prompt import build_system_prompt
        is_group = os.getenv("IS_GROUP", "False").lower() == "true"
        return build_system_prompt(
            AGENT_NAME="ARIANNA-ANCHOR",
            is_group=is_group
        )

    async def _get_thread(self, key: str) -> str:
        """Get or create a thread for the given key."""
        if key not in self.threads:
            async with httpx.AsyncClient() as client:
                try:
                    r = await client.post(
                        "https://api.openai.com/v1/threads",
                        headers=self.headers,
                        json={"metadata": {"thread_key": key}},
                        timeout=self.request_timeout,
                    )
                    r.raise_for_status()
                    self.threads[key] = r.json()["id"]
                except httpx.TimeoutException:
                    self.logger.error("OpenAI request timed out when creating thread")
                    raise
            save_threads(self.threads)
        return self.threads[key]

    async def ask(self, thread_key: str, prompt: str, is_group: bool=False) -> str:
        """
        Кладёт prompt в thread, запускает run, ждёт и возвращает ответ.
        Если ассистент запрашивает GENESIS-функцию — обрабатываем через handle_genesis_call().
        """
        tid = await self._get_thread(thread_key)

        # Добавляем пользовательский запрос
        async with httpx.AsyncClient() as client:
            try:
                msg = await client.post(
                    f"https://api.openai.com/v1/threads/{tid}/messages",
                    headers=self.headers,
                    json={"role": "user", "content": prompt, "metadata": {"is_group": str(is_group)}},
                    timeout=self.request_timeout,
                )
                msg.raise_for_status()
            except httpx.TimeoutException:
                self.logger.error("OpenAI request timed out when posting message")
                raise
            except Exception as e:
                self.logger.error("Failed to post user message", exc_info=e)
                # Try to recreate the thread in case the ID became invalid
                self.threads.pop(thread_key, None)
                tid = await self._get_thread(thread_key)
                try:
                    msg = await client.post(
                        f"https://api.openai.com/v1/threads/{tid}/messages",
                        headers=self.headers,
                        json={"role": "user", "content": prompt, "metadata": {"is_group": str(is_group)}},
                        timeout=self.request_timeout,
                    )
                    msg.raise_for_status()
                except httpx.TimeoutException:
                    self.logger.error("OpenAI request timed out after recreating thread")
                    raise
                except Exception as e2:
                    self.logger.error("Failed to post user message after recreating thread", exc_info=e2)
                    raise

            # Запускаем ассистента
            try:
                run = await client.post(
                    f"https://api.openai.com/v1/threads/{tid}/runs",
                    headers=self.headers,
                    json={"assistant_id": self.assistant_id},
                    timeout=self.request_timeout,
                )
                run.raise_for_status()
            except httpx.TimeoutException:
                self.logger.error("OpenAI request timed out when starting run")
                raise
            run_id = run.json()["id"]

            # Polling
            start_time = time.monotonic()
            while True:
                if time.monotonic() - start_time > 60:
                    self.logger.error("Polling timeout for run %s", run_id)
                    raise TimeoutError("AriannaEngine.ask() polling timed out")
                await asyncio.sleep(0.5)
                try:
                    st = await client.get(
                        f"https://api.openai.com/v1/threads/{tid}/runs/{run_id}",
                        headers=self.headers,
                        timeout=self.request_timeout,
                    )
                except httpx.TimeoutException:
                    self.logger.error("OpenAI request timed out while polling run status")
                    raise
                run_json = st.json()
                status = run_json["status"]
                if status == "requires_action":
                    tool_calls = run_json.get("required_action", {}) \
                        .get("submit_tool_outputs", {}) \
                        .get("tool_calls", [])
                    if tool_calls:
                        output = await handle_genesis_call(tool_calls)
                        try:
                            await client.post(
                                f"https://api.openai.com/v1/threads/{tid}/runs/{run_id}/submit_tool_outputs",
                                headers=self.headers,
                                json={"tool_outputs": [{
                                    "tool_call_id": tool_calls[0]["id"],
                                    "output": output
                                }]},
                                timeout=self.request_timeout,
                            )
                        except httpx.TimeoutException:
                            self.logger.error("Timeout submitting tool outputs")
                            raise
                    continue
                if status == "completed":
                    break
                if status in {"failed", "cancelled"}:
                    self.logger.error("Run %s ended with status %s", run_id, status)
                    raise RuntimeError(f"Run {run_id} {status}")

            # Получаем все tool_calls (если есть) и обычный контент
            try:
                final = await client.get(
                    f"https://api.openai.com/v1/threads/{tid}/messages",
                    headers=self.headers,
                    timeout=self.request_timeout,
                )
            except httpx.TimeoutException:
                self.logger.error("Timeout when retrieving final message")
                raise
            msg = final.json()["data"][0]
            # Если ассистент вызвал функцию GENESIS:
            if msg.get("tool_calls"):
                answer = await handle_genesis_call(msg["tool_calls"])
            else:
                answer = msg["content"][0]["text"]["value"]

            event = {
                "thread_key": thread_key,
                "prompt": prompt,
                "reply": answer,
                "timestamp": time.time(),
            }
            log_event(event)
            asyncio.create_task(log_event_pinecone(event, self.openai_key))
            return answer

    async def deepseek_reply(self, prompt: str) -> str:
        """Отправить сообщение в DeepSeek и вернуть его ответ."""
        system_prompt = self._load_system_prompt()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        reply = await call_deepseek(messages)
        if reply is None:
            return "DeepSeek did not return a response"
        return reply

