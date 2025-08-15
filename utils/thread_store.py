from .thread_db import (
    THREAD_DB_PATH,
    THREAD_JSON_PATH,
    get_conn,
    init_db,
    load_thread_map,
    migrate_from_json,
    save_thread_map,
)


def load_threads(path: str = THREAD_DB_PATH, json_path: str = THREAD_JSON_PATH) -> dict:
    """Load stored thread mappings from SQLite."""
    with get_conn(path) as conn:
        init_db(conn)
        migrate_from_json(conn, json_path)
        return load_thread_map(conn)


def save_threads(threads: dict, path: str = THREAD_DB_PATH) -> None:
    """Persist thread mappings into SQLite."""
    with get_conn(path) as conn:
        init_db(conn)
        save_thread_map(conn, threads)
