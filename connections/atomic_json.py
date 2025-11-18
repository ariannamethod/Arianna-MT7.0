import json
import os
import tempfile
from contextlib import contextmanager

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def _file_lock(path: str):
    lock_file = open(path, "a+", encoding="utf-8")
    try:
        lock_file.seek(0)
        if os.name == "nt":
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
        finally:
            lock_file.close()


def atomic_json_dump(path: str, data, **dump_kwargs) -> None:
    """Atomically write JSON data to a file with a file lock."""
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    tmp_name = None
    with _file_lock(path):
        with tempfile.NamedTemporaryFile(
            "w", dir=dir_name, delete=False, encoding="utf-8"
        ) as tmp_file:
            json.dump(data, tmp_file, **dump_kwargs)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_name = tmp_file.name
        os.replace(tmp_name, path)
        tmp_name = None
    if tmp_name and os.path.exists(tmp_name):
        try:
            os.remove(tmp_name)
        except OSError:
            pass
