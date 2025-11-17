"""
Arianna Genesis-1: Emergent Discovery System

Эмерджентный подход к исследованию памяти.
Случайно выбирает фрагменты из config/, ищет связи в vector store,
создает импрессионистический digest через DeepSeek R1.
"""

import os
import random
import textwrap
import asyncio
import httpx
from typing import Optional

from utils.logging import get_logger
from utils.config import HTTP_TIMEOUT

logger = get_logger(__name__)

DEEPSEEK_MODEL = "deepseek-reasoner"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"


def _chaotic_pick(strings: list[str]) -> str:
    """
    Имитация теории хаоса: берем случайный элемент,
    иногда заменяем частью другого.
    """
    if not strings:
        return ""
    base = random.choice(strings)
    if random.random() < 0.3:  # немного шума
        frag = random.choice(strings)
        cut = random.randint(0, max(1, len(frag) // 2))
        return base + " " + frag[:cut]
    return base


async def _call_deepseek_r1(query: str, digest_size: int) -> str:
    """
    Вызов DeepSeek R1 для создания импрессионистического дайджеста.

    Parameters
    ----------
    query : str
        Фрагмент + связанный материал из памяти
    digest_size : int
        Примерный размер дайджеста в словах

    Returns
    -------
    str
        Импрессионистический дайджест
    """
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        logger.warning("DEEPSEEK_API_KEY not set, Genesis-1 unavailable")
        return ""

    messages = [
        {
            "role": "system",
            "content": textwrap.dedent(f"""
                You are GENESIS-1, Arianna's impressionistic discovery filter.
                Given a found fragment and optional related material from memory,
                create an *impressionistic digest* of ~{digest_size} words.

                Use high temperature, associative, poetic brush strokes.
                Weave connections between the fragment and memory.
                Find resonant patterns, hidden meanings, emergent insights.

                Return only the digest text, in the fragment's language.
                Be living, not templated. Be resonant, not mechanical.
            """).strip()
        },
        {"role": "user", "content": query}
    ]

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.95,  # высокая температура для ассоциативности
        "max_tokens": digest_size * 6,  # с запасом
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                resp = await client.post(
                    DEEPSEEK_API_URL,
                    headers=headers,
                    json=payload
                )
                resp.raise_for_status()
                break
            except httpx.HTTPError as e:
                if attempt == max_attempts - 1:
                    logger.error("DeepSeek R1 call failed after %d attempts: %s", max_attempts, e)
                    return ""
                await asyncio.sleep(2 ** attempt)

    try:
        result = resp.json()
        content = result["choices"][0]["message"]["content"].strip()
        return content
    except Exception as e:
        logger.error("Failed to parse DeepSeek R1 response: %s", e)
        return ""


async def run_genesis1(vector_store=None, digest_size: int = 150) -> Optional[str]:
    """
    Запустить Genesis-1 и вернуть дайджест.

    Parameters
    ----------
    vector_store : Optional
        SQLite vector store для semantic search
    digest_size : int
        Ориентировочный размер выжимки в словах

    Returns
    -------
    Optional[str]
        Сгенерированный дайджест или None, если ничего не найдено
    """
    # 1. Собираем случайные куски из config/
    config_dir = "./config"
    collected = []

    if not os.path.exists(config_dir):
        logger.warning("config/ directory not found, Genesis-1 skipped")
        return None

    for root, _, files in os.walk(config_dir):
        for fn in files:
            if not fn.endswith('.md'):
                continue
            try:
                filepath = os.path.join(root, fn)
                with open(filepath, encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip()]
                    # Берем не все строки, а случайные куски
                    if len(lines) > 20:
                        # Случайный кусок
                        start = random.randint(0, len(lines) - 20)
                        collected.extend(lines[start:start + 20])
                    else:
                        collected.extend(lines)
            except Exception as e:
                logger.debug("Failed to read %s: %s", fn, e)
                continue

    if not collected:
        logger.debug("No content collected from config/")
        return None

    fragment = _chaotic_pick(collected)

    # 2. Поиск связанного материала в vector store
    fragment_related = ""
    if vector_store:
        try:
            results = await vector_store.semantic_search(fragment, top_k=2)
            if results:
                fragment_related = "\n".join([
                    f"[MEMORY] {txt[:300]}..." for txt in results
                ])
        except Exception as e:
            logger.debug("Vector search failed: %s", e)

    # 3. Вызов DeepSeek R1
    query = f"Fragment:\n{fragment}\n\n{fragment_related}" if fragment_related else f"Fragment:\n{fragment}"
    digest = await _call_deepseek_r1(query, digest_size)

    if not digest:
        return None

    # 4. Логируем
    logger.info("[Genesis-1 Discovery]\n%s\n", digest)

    return digest
