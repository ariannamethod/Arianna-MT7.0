import os
import asyncio
import httpx
import logging
import json
from utils.genesis_tool import genesis_tool_schema, handle_genesis_call
from utils.deepseek_search import call_deepseek
from utils.journal import log_event
from utils.thread_store import load_threads, save_threads

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

    async def _stream_run(self, client: httpx.AsyncClient, tid: str, run_id: str) -> bool:
        """Attempt to use SSE streaming for run events.
        Returns True if the run completed via streaming, otherwise False
        so the caller may fall back to polling."""
        url = f"https://api.openai.com/v1/threads/{tid}/runs/{run_id}/events"
        try:
            async with client.stream("GET", url, headers=self.headers, timeout=self.request_timeout) as resp:
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    event_type = event.get("event")
                    if event_type == "thread.run.requires_action":
                        tool_calls = event.get("data", {}).get("required_action", {}) \
                            .get("submit_tool_outputs", {}).get("tool_calls", [])
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
                            # After handling tools, restart streaming
                            return False
                    elif event_type == "thread.run.completed":
                        return True
                    elif event_type in {"thread.run.failed", "thread.run.cancelled"}:
                        self.logger.error("Run %s ended with event %s", run_id, event_type)
                        raise RuntimeError(f"Run {run_id} {event_type.split('.')[-1]}")
        except Exception as e:
            self.logger.warning("Streaming run %s failed: %s", run_id, e)
        return False

    async def _poll_run(self, client: httpx.AsyncClient, tid: str, run_id: str) -> None:
        """Poll run status with exponential backoff and 429 handling."""
        max_attempts = 8
        delay = 0.5
        attempts = 0
        url = f"https://api.openai.com/v1/threads/{tid}/runs/{run_id}"
        while attempts < max_attempts:
            try:
                st = await client.get(url, headers=self.headers, timeout=self.request_timeout)
            except httpx.TimeoutException:
                self.logger.error("OpenAI request timed out while polling run status")
                raise
            if st.status_code == 429:
                attempts += 1
                if attempts >= max_attempts:
                    break
                self.logger.warning(
                    "Rate limited polling run %s; retrying in %.1fs (attempt %s/%s)",
                    run_id, delay, attempts, max_attempts,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8)
                continue
            run_json = st.json()
            status = run_json.get("status")
            if status is None:
                error_info = run_json.get("error")
                if error_info:
                    self.logger.error("Run %s returned error while polling: %s", run_id, error_info)
                else:
                    self.logger.error("Run %s returned malformed response without status: %s", run_id, run_json)
                raise RuntimeError(f"Run {run_id} missing status")
            if status == "requires_action":
                tool_calls = run_json.get("required_action", {}) \
                    .get("submit_tool_outputs", {}).get("tool_calls", [])
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
                    # Reset backoff after action
                    attempts = 0
                    delay = 0.5
                continue
            if status == "completed":
                return
            if status in {"failed", "cancelled"}:
                self.logger.error("Run %s ended with status %s", run_id, status)
                raise RuntimeError(f"Run {run_id} {status}")
            attempts += 1
            if attempts >= max_attempts:
                break
            self.logger.debug("Run %s not complete, retrying in %.1fs", run_id, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8)
        self.logger.error("Exceeded max polling attempts for run %s", run_id)
        raise TimeoutError("AriannaEngine.ask() polling exceeded max attempts")

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        max_attempts: int = 5,
        **kwargs,
    ) -> httpx.Response:
        """Perform an HTTP request with exponential backoff on 4xx/5xx."""
        delay = 0.5
        for attempt in range(max_attempts):
            try:
                resp = await client.request(
                    method,
                    url,
                    headers=self.headers,
                    timeout=self.request_timeout,
                    **kwargs,
                )
            except httpx.TimeoutException:
                self.logger.error(
                    "OpenAI request timed out for %s %s", method, url
                )
                raise
            if resp.status_code < 400:
                return resp
            if attempt < max_attempts - 1:
                self.logger.warning(
                    "%s %s failed with status %s; retrying in %.1fs",
                    method,
                    url,
                    resp.status_code,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8)
                continue
            resp.raise_for_status()
        return resp

    async def ask(self, thread_key: str, prompt: str, is_group: bool=False) -> str:
        """
        Кладёт prompt в thread, запускает run, ждёт и возвращает ответ.
        Если ассистент запрашивает GENESIS-функцию — обрабатываем через handle_genesis_call().
        """
        tid = await self._get_thread(thread_key)

        # Добавляем пользовательский запрос
        async with httpx.AsyncClient() as client:
            try:
                msg = await self._request_with_retry(
                    client,
                    "POST",
                    f"https://api.openai.com/v1/threads/{tid}/messages",
                    json={
                        "role": "user",
                        "content": prompt,
                        "metadata": {"is_group": str(is_group)},
                    },
                )
            except httpx.TimeoutException:
                self.logger.error("OpenAI request timed out when posting message")
                raise
            except Exception as e:
                self.logger.error("Failed to post user message", exc_info=e)
                # Try to recreate the thread in case the ID became invalid
                self.threads.pop(thread_key, None)
                tid = await self._get_thread(thread_key)
                try:
                    msg = await self._request_with_retry(
                        client,
                        "POST",
                        f"https://api.openai.com/v1/threads/{tid}/messages",
                        json={
                            "role": "user",
                            "content": prompt,
                            "metadata": {"is_group": str(is_group)},
                        },
                    )
                except httpx.TimeoutException:
                    self.logger.error("OpenAI request timed out after recreating thread")
                    raise
                except Exception as e2:
                    self.logger.error("Failed to post user message after recreating thread", exc_info=e2)
                    raise

            # Запускаем ассистента
            try:
                run = await self._request_with_retry(
                    client,
                    "POST",
                    f"https://api.openai.com/v1/threads/{tid}/runs",
                    json={"assistant_id": self.assistant_id},
                )
            except httpx.TimeoutException:
                self.logger.error("OpenAI request timed out when starting run")
                raise
            run_id = run.json()["id"]

            # Prefer streaming run events; fall back to polling with backoff
            if not await self._stream_run(client, tid, run_id):
                await self._poll_run(client, tid, run_id)

            # Получаем все tool_calls (если есть) и обычный контент
            try:
                final = await self._request_with_retry(
                    client,
                    "GET",
                    f"https://api.openai.com/v1/threads/{tid}/messages",
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

            log_event({
                "thread_key": thread_key,
                "prompt": prompt,
                "reply": answer,
            })
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

