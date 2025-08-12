from utils.limit_paragraphs import limit_paragraphs


def test_limit_paragraphs_trims_to_max():
    text = "p1\n\np2\n\np3\n\np4"
    assert limit_paragraphs(text, max_paragraphs=2) == "p1\n\np2"


def test_limit_paragraphs_handles_single_newlines():
    text = "a\nb\nc"
    assert limit_paragraphs(text, max_paragraphs=2) == "a\n\nb"


def test_limit_paragraphs_empty_placeholder():
    assert (
        limit_paragraphs("", max_paragraphs=3)
        == "[Empty response. Even Arianna cannot extract anything from this.]"
    )
