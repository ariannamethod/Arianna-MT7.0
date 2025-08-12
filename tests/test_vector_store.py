import asyncio

import utils.vector_store as vs
from utils.vector_store import chunk_text, VectorStore


def test_chunk_text_splits_with_overlap():
    text = "abcdefghij"
    chunks = chunk_text(text, chunk_size=6, overlap=2)
    assert chunks == ["abcdef", "efghij", "ij"]


def test_chunk_text_ignores_empty_chunks():
    text = "     "
    assert chunk_text(text, chunk_size=3, overlap=1) == []


def test_vectorize_all_files_with_mocks(monkeypatch, tmp_path):
    file = tmp_path / "test.md"
    file.write_text("hello world")

    def fake_scan_files(path="config/*.md"):
        return {str(file): "hash"}

    def fake_load_vector_meta():
        return {}

    recorded_meta = {}

    def fake_save_vector_meta(meta):
        recorded_meta.update(meta)

    async def fake_safe_embed(text, client):
        return [0.0] * vs.EMBED_DIM

    class DummyIndex:
        def __init__(self):
            self.upserts = []

        def upsert(self, vectors):
            self.upserts.extend(vectors)

    index = DummyIndex()

    class DummyOpenAI:
        pass

    store = VectorStore(openai_client=DummyOpenAI(), backend=index)

    monkeypatch.setattr(vs, "scan_files", fake_scan_files)
    monkeypatch.setattr(vs, "load_vector_meta", fake_load_vector_meta)
    monkeypatch.setattr(vs, "save_vector_meta", fake_save_vector_meta)
    monkeypatch.setattr(vs, "safe_embed", fake_safe_embed)

    result = asyncio.run(store.vectorize_all_files(force=True))
    assert index.upserts
    assert "upserted" in result
