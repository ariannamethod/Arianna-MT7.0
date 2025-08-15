from utils.text_helpers import _extract_links


def test_extract_links_filters_domains_and_keywords():
    html = (
        "<a href='https://example.com/article'>Example</a>"
        "<a href='https://example.com/news'>News</a>"
        "<a href='https://test.com/page'>Test</a>"
    )
    links = _extract_links(html, allowed_domains={"example.com"}, keywords={"news"})
    assert links == ["https://example.com/news"]


def test_extract_links_raw_text_fallback():
    text = "Check https://example.com and https://another.com"
    links = _extract_links(text, allowed_domains={"example.com"})
    assert links == ["https://example.com"]
