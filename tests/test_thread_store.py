import json
from utils.thread_store import load_threads, save_threads


def test_save_and_load(tmp_path):
    db_path = tmp_path / "threads.db"
    data = {"user": "tid"}
    save_threads(data, path=str(db_path))
    loaded = load_threads(path=str(db_path))
    assert loaded == data


def test_migration(tmp_path):
    json_file = tmp_path / "threads.json"
    db_path = tmp_path / "threads.db"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump({"u1": "t1"}, f)
    loaded = load_threads(path=str(db_path), json_path=str(json_file))
    assert loaded == {"u1": "t1"}
    assert not json_file.exists()
    loaded_again = load_threads(path=str(db_path), json_path=str(json_file))
    assert loaded_again == {"u1": "t1"}
