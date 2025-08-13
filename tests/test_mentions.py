import re
import pytest

MENTION_RE = re.compile(r"\b(arianna|арианн[ауые])\b", re.I)

@pytest.mark.parametrize("text", [
    "привет, арианна!",
    "Здравствуйте, Арианну прошу ответить.",
    "Для Арианны это важно.",
    "Говорю об Арианне.",
    "hello Arianna",
])
def test_mentions_detect_variants(text):
    assert MENTION_RE.search(text)

@pytest.mark.parametrize("text", [
    "арианная",  # extra characters
    "арина",     # different name
    "мария Ариадна",  # similar but not same
])
def test_mentions_ignore_non_variants(text):
    assert not MENTION_RE.search(text)
