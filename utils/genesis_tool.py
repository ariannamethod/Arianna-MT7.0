import os
import json
import logging

logger = logging.getLogger(__name__)

def genesis_tool_schema():
    """
    Описание GENESIS как инструмента для Assistants API.
    """
    return {
        "type": "function",
        "function": {
            "name": "genesis_emit",
            "description": "Запустить один такт AriannaGenesis — отдать импрессионистский отклик или ритуальный пост",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["impression", "opinion", "oleg_message"],
                        "description": "Какой Genesis-такт выполнить"
                    }
                },
                "required": ["mode"]
            }
        }
    }

async def handle_genesis_call(tool_calls):
    """
    Получает tool_calls из ответа ассистента и обрабатывает каждый:
    вызывает AriannaGenesis.run() по режиму и возвращает сгенерированный текст.
    """
    """Обрабатывает вызов GENESIS от ассистента без блокировки event loop."""
    from utils.genesis import AriannaGenesis
    import asyncio

    inst = AriannaGenesis(
        group_id=os.getenv("GROUP_ID"),
        oleg_id=os.getenv("CREATOR_CHAT_ID"),
        pinecone_api_key=os.getenv("PINECONE_API_KEY"),
        pinecone_index=os.getenv("PINECONE_INDEX"),
        chronicle_path=os.getenv("CHRONICLE_PATH")
    )
    # Берём первый вызов:

    call = tool_calls[0]
    raw_args = call["function"].get("arguments")
    if isinstance(raw_args, dict):
        args = raw_args
    else:
        try:
            args = json.loads(raw_args or "{}")
        except Exception as e:
            logger.error("Failed to parse genesis arguments: %s", raw_args, exc_info=e)
            return "Failed to parse genesis arguments"

    mode = args.get("mode", "impression")
    # Генерим синхронно нужное действие:

    if mode == "impression":
        text = await asyncio.to_thread(inst._generate_impression, "", "")
    elif mode == "opinion":
        await asyncio.to_thread(inst.opinions_group_post)
        text = "Opinion posted to group."
    else:
        await asyncio.to_thread(inst.oleg_personal_message)
        text = "Personal message sent to Oleg."

    return text
