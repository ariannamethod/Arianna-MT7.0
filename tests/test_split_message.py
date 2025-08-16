import pytest
from utils.split_message import split_message


def test_split_message_splits_long_text():
    text = "a" * 5000
    parts = split_message(text, max_length=4000)
    assert len(parts) == 2
    assert parts[0] == "a" * 4000
    assert parts[1] == "a" * 1000


def test_split_message_empty_returns_empty_list():
    assert split_message("", max_length=10) == []


def test_split_message_prefers_newline():
    text = "line1\nline2\nline3"
    parts = split_message(text, max_length=7)
    assert parts == ["line1", "line2", "line3"]
