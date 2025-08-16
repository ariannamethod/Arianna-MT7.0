import importlib


def test_semantic_query(monkeypatch, tmp_path):
    mem_path = tmp_path / "memory.db"
    monkeypatch.setenv("MEMORY_DB_PATH", str(mem_path))
    memory = importlib.reload(importlib.import_module("utils.memory"))

    def fake_embed(text: str):
        text = text.lower()
        if "cat" in text:
            return [1.0, 0.0]
        if "dog" in text:
            return [0.0, 1.0]
        return [0.5, 0.5]

    monkeypatch.setattr(memory, "_embed_text", fake_embed)

    memory.add_event("note", "I love cats")
    memory.add_event("note", "Dogs are great")

    res = memory.semantic_query("cat")
    assert res[0]["content"] == "I love cats"
