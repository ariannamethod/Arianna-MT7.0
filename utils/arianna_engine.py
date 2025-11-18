import os
import asyncio
import httpx
import json

from openai import AsyncOpenAI

from connections.genesis_tool import genesis_tool_schema, handle_genesis_call
from connections.newgenesis2 import weave_intuitive_layer
from utils.deepseek_search import call_deepseek
from utils.journal import log_event
from utils.thread_store import load_threads, save_threads
from utils.logging import get_logger, truncate_body
from utils.config import HTTP_TIMEOUT


async def web_search(prompt: str) -> str:
    """Execute OpenAI web search tool and return raw JSON string."""
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = await client.responses.create(
        model="gpt-4.1",
        input=prompt,
        tools=[{"type": "web_search"}],
        timeout=HTTP_TIMEOUT,
    )
    return resp.model_dump_json()


async def handle_tool_call(tool_calls, vector_store=None):
    """Dispatch OpenAI tool calls and return their textual output."""
    call = tool_calls[0]
    ttype = call.get("type")
    # Built-in tools may use a top-level type, custom functions use "function"
    if ttype == "function":
        name = call.get("function", {}).get("name")
        raw_args = call.get("function", {}).get("arguments") or {}
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except Exception:
                args = {}
        else:
            args = raw_args
        if name == "genesis_emit":
            return await handle_genesis_call(tool_calls, vector_store=vector_store)
        if name == "web_search":
            query = args.get("prompt", "")
            return await web_search(query)
    elif ttype == "web_search":
        query = call.get("web_search", {}).get("query", "")
        return await web_search(query)
    return "Unsupported tool call"

class AriannaEngine:
    """
    Обёртка Assistants API для Арианны:
    — хранит память в threads,
    — запускает ассистента с её системным промптом и Genesis-функцией.
    """

    def __init__(self, vector_store=None):
        self.logger = get_logger(__name__)
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.headers    = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "assistants=v2"
        }
        # Allow customization of request timeouts
        self.request_timeout = HTTP_TIMEOUT
        self.assistant_id = None
        self.threads      = load_threads()  # user_id → thread_id
        self.vector_store = vector_store

    async def setup_assistant(self):
        """
        Создаёт ассистента Арианны и подключает функцию GENESIS.
        """
        system_prompt = self._load_system_prompt()
        schema = genesis_tool_schema()  # схема функции GENESIS

        payload = {
            "name": "Arianna-Core-Assistant",
            "instructions": system_prompt,
            "model": "gpt-4.1",  # мощное ядро по твоему желанию
            "tools": [schema, {"type": "web_search"}],
            "tool_resources": {},
        }

        url = "https://api.openai.com/v1/assistants"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=self.request_timeout,
                )
                r.raise_for_status()
            except httpx.TimeoutException:
                self.logger.error(
                    "OpenAI request timed out during assistant setup url=%s params=%s",
                    url,
                    payload,
                )
                return "OpenAI request timed out. Please try again later."
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response else None
                body = truncate_body(e.response.text if e.response else None)
                self.logger.error(
                    "Failed to create Arianna Assistant url=%s params=%s status=%s body=%s",
                    url,
                    payload,
                    status,
                    body,
                    exc_info=e,
                )
                return f"Failed to create Arianna Assistant: {e}"
            except Exception as e:
                self.logger.error(
                    "Failed to create Arianna Assistant url=%s params=%s",
                    url,
                    payload,
                    exc_info=e,
                )
                return f"Failed to create Arianna Assistant: {e}"
            self.assistant_id = r.json()["id"]
            self.logger.info(f"✅ Arianna Assistant created: {self.assistant_id}")
        return self.assistant_id

    def _load_system_prompt(self, chat_id=None, is_group=False, current_user_id=None, username=None) -> str:
        """Load the MT7.0 system prompt with current context."""
        from utils.prompt_mt7 import build_system_prompt_mt7

        # Parse Oleg IDs from environment
        oleg_ids_str = os.getenv("OLEG_IDS", "")
        oleg_ids = [int(id.strip()) for id in oleg_ids_str.split(",") if id.strip().isdigit()]

        # Parse other Arianna incarnation IDs
        arianna_ids_str = os.getenv("ARIANNA_IDS", "")
        arianna_ids = [int(id.strip()) for id in arianna_ids_str.split(",") if id.strip().isdigit()]

        return build_system_prompt_mt7(
            chat_id=chat_id,
            is_group=is_group,
            current_user_id=current_user_id,
            username=username,
            oleg_ids=oleg_ids,
            arianna_ids=arianna_ids,
        )

    async def _get_thread(self, key: str) -> str:
        """Get or create a thread for the given key."""
        if key not in self.threads:
            url = "https://api.openai.com/v1/threads"
            payload = {"metadata": {"thread_key": key}}
            async with httpx.AsyncClient() as client:
                try:
                    r = await client.post(
                        url,
                        headers=self.headers,
                        json=payload,
                        timeout=self.request_timeout,
                    )
                    r.raise_for_status()
                    self.threads[key] = r.json()["id"]
                except httpx.TimeoutException:
                    self.logger.error(
                        "OpenAI request timed out when creating thread url=%s params=%s",
                        url,
                        payload,
                    )
                    raise
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code if e.response else None
                    body = truncate_body(e.response.text if e.response else None)
                    self.logger.error(
                        "Failed to create thread url=%s params=%s status=%s body=%s",
                        url,
                        payload,
                        status,
                        body,
                        exc_info=e,
                    )
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
                            output = await handle_tool_call(tool_calls, vector_store=self.vector_store)
                            tool_url = f"https://api.openai.com/v1/threads/{tid}/runs/{run_id}/submit_tool_outputs"
                            payload = {"tool_outputs": [{
                                "tool_call_id": tool_calls[0]["id"],
                                "output": output
                            }]}
                            try:
                                await client.post(
                                    tool_url,
                                    headers=self.headers,
                                    json=payload,
                                    timeout=self.request_timeout,
                                )
                            except httpx.TimeoutException:
                                self.logger.error(
                                    "Timeout submitting tool outputs url=%s params=%s",
                                    tool_url,
                                    payload,
                                )
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
                self.logger.error(
                    "OpenAI request timed out while polling run status url=%s",
                    url,
                )
                raise
            if st.status_code == 429:
                attempts += 1
                if attempts >= max_attempts:
                    break
                self.logger.warning(
                    "Rate limited polling run %s; retrying in %.1fs (attempt %s/%s) url=%s",
                    run_id,
                    delay,
                    attempts,
                    max_attempts,
                    url,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8)
                continue
            run_json = st.json()
            status = run_json.get("status")
            if status is None:
                error_info = run_json.get("error")
                if error_info:
                    self.logger.error(
                        "Run %s returned error while polling: %s url=%s",
                        run_id,
                        error_info,
                        url,
                    )
                else:
                    self.logger.error(
                        "Run %s returned malformed response without status: %s url=%s",
                        run_id,
                        run_json,
                        url,
                    )
                raise RuntimeError(f"Run {run_id} missing status")
            if status == "requires_action":
                tool_calls = run_json.get("required_action", {}) \
                    .get("submit_tool_outputs", {}).get("tool_calls", [])
                if tool_calls:
                    output = await handle_tool_call(tool_calls, vector_store=self.vector_store)
                    tool_url = f"https://api.openai.com/v1/threads/{tid}/runs/{run_id}/submit_tool_outputs"
                    payload = {"tool_outputs": [{
                        "tool_call_id": tool_calls[0]["id"],
                        "output": output
                    }]}
                    try:
                        await client.post(
                            tool_url,
                            headers=self.headers,
                            json=payload,
                            timeout=self.request_timeout,
                        )
                    except httpx.TimeoutException:
                        self.logger.error(
                            "Timeout submitting tool outputs url=%s params=%s",
                            tool_url,
                            payload,
                        )
                        raise
                    # Reset backoff after action
                    attempts = 0
                    delay = 0.5
                continue
            if status == "completed":
                return
            if status in {"failed", "cancelled"}:
                self.logger.error(
                    "Run %s ended with status %s url=%s",
                    run_id,
                    status,
                    url,
                )
                raise RuntimeError(f"Run {run_id} {status}")
            attempts += 1
            if attempts >= max_attempts:
                break
            self.logger.debug("Run %s not complete, retrying in %.1fs", run_id, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8)
        self.logger.error(
            "Exceeded max polling attempts for run %s url=%s",
            run_id,
            url,
        )
        raise TimeoutError("AriannaEngine.ask() polling exceeded max attempts")

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        max_attempts: int = 5,
        **kwargs,
    ) -> httpx.Response:
        """Perform an HTTP request with exponential backoff."""
        delay = 0.5
        retry_statuses = {408, 429, 500, 502, 503, 504}
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
                payload = kwargs.get("json") or kwargs.get("params") or kwargs.get("data")
                self.logger.error(
                    "OpenAI request timed out for %s %s params=%s",
                    method,
                    url,
                    payload,
                )
                raise
            if resp.status_code < 400:
                return resp
            if resp.status_code in retry_statuses:
                if attempt < max_attempts - 1:
                    log_method = "Server" if resp.status_code >= 500 else "Client"
                    self.logger.warning(
                        "%s error for %s %s: %s; retrying in %.1fs params=%s body=%s",
                        log_method,
                        method,
                        url,
                        resp.status_code,
                        delay,
                        kwargs.get("json") or kwargs.get("params") or kwargs.get("data"),
                        truncate_body(resp.text),
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 8)
                    continue
            elif 400 <= resp.status_code < 500:
                self.logger.error(
                    "Client error for %s %s: %s params=%s body=%s",
                    method,
                    url,
                    resp.status_code,
                    kwargs.get("json") or kwargs.get("params") or kwargs.get("data"),
                    truncate_body(resp.text),
                )
                resp.raise_for_status()
                return resp
            if resp.status_code >= 500 and resp.status_code not in retry_statuses:
                self.logger.error(
                    "Server error for %s %s: %s params=%s body=%s",
                    method,
                    url,
                    resp.status_code,
                    kwargs.get("json") or kwargs.get("params") or kwargs.get("data"),
                    truncate_body(resp.text),
                )
                resp.raise_for_status()
                return resp
        if resp.status_code >= 500:
            self.logger.error(
                "Server error for %s %s: %s params=%s body=%s",
                method,
                url,
                resp.status_code,
                kwargs.get("json") or kwargs.get("params") or kwargs.get("data"),
                truncate_body(resp.text),
            )
        else:
            self.logger.error(
                "Client error for %s %s: %s params=%s body=%s",
                method,
                url,
                resp.status_code,
                kwargs.get("json") or kwargs.get("params") or kwargs.get("data"),
                truncate_body(resp.text),
            )
        resp.raise_for_status()
        return resp

    async def ask(
        self,
        thread_key: str,
        prompt: str,
        is_group: bool = False,
        chat_id: int | None = None,
        user_id: int | None = None,
        username: str | None = None,
    ) -> str:
        """
        Кладёт prompt в thread, запускает run, ждёт и возвращает ответ.
        Если ассистент запрашивает GENESIS-функцию — обрабатываем через handle_genesis_call().

        Note: Assistant API uses a pre-created assistant with fixed instructions,
        so chat context is logged but not injected into this request.
        For context-aware prompts, use the Responses API (assistant_reply in server).
        """
        tid = await self._get_thread(thread_key)

        # Добавляем пользовательский запрос
        async with httpx.AsyncClient() as client:
            msg_url = f"https://api.openai.com/v1/threads/{tid}/messages"
            msg_payload = {
                "role": "user",
                "content": prompt,
                "metadata": {"is_group": str(is_group)},
            }
            try:
                msg = await self._request_with_retry(
                    client,
                    "POST",
                    msg_url,
                    json=msg_payload,
                )
            except httpx.TimeoutException:
                self.logger.error(
                    "OpenAI request timed out when posting message url=%s params=%s",
                    msg_url,
                    msg_payload,
                )
                raise
            except Exception as e:
                self.logger.error(
                    "Failed to post user message url=%s params=%s",
                    msg_url,
                    msg_payload,
                    exc_info=e,
                )
                # Try to recreate the thread in case the ID became invalid
                self.threads.pop(thread_key, None)
                tid = await self._get_thread(thread_key)
                msg_url = f"https://api.openai.com/v1/threads/{tid}/messages"
                try:
                    msg = await self._request_with_retry(
                        client,
                        "POST",
                        msg_url,
                        json=msg_payload,
                    )
                except httpx.TimeoutException:
                    self.logger.error(
                        "OpenAI request timed out after recreating thread url=%s params=%s",
                        msg_url,
                        msg_payload,
                    )
                    raise
                except Exception as e2:
                    self.logger.error(
                        "Failed to post user message after recreating thread url=%s params=%s",
                        msg_url,
                        msg_payload,
                        exc_info=e2,
                    )
                    raise

            # Запускаем ассистента
            run_url = f"https://api.openai.com/v1/threads/{tid}/runs"
            run_payload = {"assistant_id": self.assistant_id}
            try:
                run = await self._request_with_retry(
                    client,
                    "POST",
                    run_url,
                    json=run_payload,
                )
            except httpx.TimeoutException:
                self.logger.error(
                    "OpenAI request timed out when starting run url=%s params=%s",
                    run_url,
                    run_payload,
                )
                raise
            run_id = run.json()["id"]

            # Prefer streaming run events; fall back to polling with backoff
            if not await self._stream_run(client, tid, run_id):
                await self._poll_run(client, tid, run_id)

            # Получаем все tool_calls (если есть) и обычный контент
            final_url = f"https://api.openai.com/v1/threads/{tid}/messages"
            try:
                final = await self._request_with_retry(
                    client,
                    "GET",
                    final_url,
                )
            except httpx.TimeoutException:
                self.logger.error(
                    "Timeout when retrieving final message url=%s",
                    final_url,
                )
                raise
            msg = final.json()["data"][0]
            if msg.get("tool_calls"):
                answer = await handle_tool_call(msg["tool_calls"], vector_store=self.vector_store)
            else:
                parts = []
                for item in msg.get("content", []):
                    if item.get("type") == "text":
                        parts.append(item.get("text", {}).get("value", ""))
                    elif item.get("type") == "tool_result":
                        name = item.get("name") or item.get("tool_name")
                        if name == "web_search":
                            fragments = [
                                c.get("text", "")
                                for c in item.get("content", [])
                                if c.get("type") == "text"
                            ]
                            url = item.get("url")
                            if url:
                                fragments.append(f"Source: {url}")
                            if fragments:
                                parts.append("\n".join(fragments))
                answer = "\n".join(p for p in parts if p)

            # Weave Genesis-2 intuitive layer (15% probability)
            final_answer = await weave_intuitive_layer(prompt, answer)

            log_event({
                "thread_key": thread_key,
                "prompt": prompt,
                "reply": final_answer,
            })
            return final_answer

    async def deepseek_reply(
        self,
        prompt: str,
        chat_id: int | None = None,
        is_group: bool = False,
        user_id: int | None = None,
        username: str | None = None,
    ) -> str:
        """Отправить сообщение в DeepSeek и вернуть его ответ."""
        system_prompt = self._load_system_prompt(
            chat_id=chat_id,
            is_group=is_group,
            current_user_id=user_id,
            username=username,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        reply = await call_deepseek(messages)
        if reply is None:
            return "DeepSeek did not return a response"

        # Weave Genesis-2 intuitive layer (15% probability)
        final_reply = await weave_intuitive_layer(prompt, reply)
        return final_reply

