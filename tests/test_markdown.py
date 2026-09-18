from core.markdown import render


def test_escape_and_inline():
    html = render("Use **bold** and `code` and <script>alert(1)</script>")
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_blocks():
    html = render("# Title\n\n- one\n- two\n\n1. a\n2. b\n\n```\nopen 1abc\n```\n\npara")
    assert "<h3>Title</h3>" in html
    assert html.count("<li>") == 4 and "<ul>" in html and "<ol>" in html
    assert "<pre><code>open 1abc</code></pre>" in html
    assert "<p>para</p>" in html


def test_link():
    html = render("see [docs](https://example.org/x) now")
    assert '<a href="https://example.org/x" target="_blank">docs</a>' in html
    assert "<a" not in render("[x](javascript:alert(1))")


def test_link_href_is_quote_escaped():
    html = render('[x](https://a.b/c"onclick="alert(1))')
    assert "<a " not in html  # a URL containing a quote is not turned into a link at all
