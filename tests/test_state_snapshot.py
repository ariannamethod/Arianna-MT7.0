import json
import asyncio

from utils.state_snapshot import StateSnapshotter
from utils.vector_store import EMBED_DIM


def test_daily_snapshot_vector_comparison(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    log_file = tmp_path / "chronicle.log"
    local_store = tmp_path / "state.json"
    ss = StateSnapshotter(chronicle_path=str(log_file), local_store=str(local_store))

    states = [
        {
            "memory": {"text": "m1", "entropy": 0.0, "perplexity": 0.0},
            "logs": {"text": "l1", "entropy": 0.0, "perplexity": 0.0},
            "resources": {"text": "r1", "entropy": 0.0, "perplexity": 0.0},
        },
        {
            "memory": {"text": "m2", "entropy": 0.0, "perplexity": 0.0},
            "logs": {"text": "l2", "entropy": 0.0, "perplexity": 0.0},
            "resources": {"text": "r2", "entropy": 0.0, "perplexity": 0.0},
        },
    ]
    call = {"i": 0}

    def fake_collect_state(self):
        return states[call["i"]]

    vectors = [
        [1.0, 0.0], [1.0, 0.0], [1.0, 0.0],
        [0.0, 1.0], [0.0, 1.0], [0.0, 1.0],
    ]
    emb_call = {"i": 0}

    async def fake_embed(self, text: str):
        v = vectors[emb_call["i"]]
        emb_call["i"] += 1
        return v + [0.0] * (EMBED_DIM - len(v))

    monkeypatch.setattr(StateSnapshotter, "collect_state", fake_collect_state)
    monkeypatch.setattr(StateSnapshotter, "_embed_text", fake_embed)

    asyncio.run(ss.run_once())
    call["i"] = 1
    asyncio.run(ss.run_once())

    data = json.loads(local_store.read_text())
    memory_entries = [d for d in data if d["block"] == "memory"]
    assert len(memory_entries) == 2
    sim = ss._cosine(memory_entries[0]["embedding"], memory_entries[1]["embedding"])
    assert sim == 0.0
