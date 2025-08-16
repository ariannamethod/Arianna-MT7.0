import asyncio
import httpx

from utils.genesis import AriannaGenesis


def test_search_and_fetch(monkeypatch, tmp_path):
    log_file = tmp_path / "log.txt"
    gen = AriannaGenesis("g", "o", chronicle_path=str(log_file))

    async def fake_web_search(self, topic: str):
        async def handler(request):
            return httpx.Response(200, json={"text": f"article about {topic}", "url": "http://example.com"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.get("https://any")
            data = resp.json()
        return data["text"], data["url"]

    monkeypatch.setattr(AriannaGenesis, "_web_search_openai", fake_web_search)
    monkeypatch.setattr(AriannaGenesis, "_generate_impression", lambda self, text, topic: "resonance")

    text, url, resonance = asyncio.run(gen._search_and_fetch("ai"))
    assert text == "article about ai"
    assert url == "http://example.com"
    assert resonance == "resonance"


def test_opinions_group_post(monkeypatch, tmp_path):
    log_file = tmp_path / "log.txt"
    gen = AriannaGenesis("g", "o", chronicle_path=str(log_file))
    gen._impressions_today = [
        {"topic": "t1", "resonance": "short", "text": "text1", "url": "u1"},
        {"topic": "t2", "resonance": "much longer resonance", "text": "text2", "url": "u2"},
    ]

    monkeypatch.setattr(AriannaGenesis, "_summarize_text", lambda self, text: "summary")
    import utils.genesis_service as gs
    monkeypatch.setattr(gs, "_EVENT_DB_PATH", str(tmp_path / "events.db"), raising=False)
    sent = {}

    async def fake_send(self, text: str):
        async def handler(request):
            sent["payload"] = request.content
            return httpx.Response(200)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await client.post("https://api.telegram.org/send", data={"chat_id": self.group_id, "text": text})
        sent["text"] = text

    monkeypatch.setattr(AriannaGenesis, "_send_to_group", fake_send)

    asyncio.run(gen.opinions_group_post())
    assert "much longer resonance" in sent["text"]
    assert sent["payload"]
