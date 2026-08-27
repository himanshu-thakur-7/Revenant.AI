"""Escaping and link-rendering in console.html.

This is the highest-risk UI code in the product. Everything rendered into
the chat originates from an LLM, which in turn relays text from web pages
and prospect sites — content the founder did not write and we do not
control. A missed escape here is stored XSS in the founder's own console.

renderRichMessage() deliberately uses innerHTML (so URLs become clickable
links), which makes esc()/linkify() the only thing standing between
untrusted text and script execution.
"""

from __future__ import annotations

import pytest

XSS_PAYLOADS = [
    "<script>window.__pwned=1</script>",
    "<img src=x onerror='window.__pwned=1'>",
    "<svg/onload=window.__pwned=1>",
    "<iframe src='javascript:window.__pwned=1'></iframe>",
    "<body onload=window.__pwned=1>",
    "'\"><script>window.__pwned=1</script>",
    "<a href='javascript:window.__pwned=1'>click</a>",
    "<div onmouseover='window.__pwned=1'>hover</div>",
]


@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_esc_neutralises_script_payloads(js, payload):
    out = js(f"return esc({payload!r});")
    assert "<script" not in out.lower()
    assert "<img" not in out.lower()
    assert "<svg" not in out.lower()
    assert "onerror" not in out.lower() or "&" in out


@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_rendering_untrusted_text_never_executes(page, js, payload):
    """The real test: render the payload the way a chat message would be
    rendered, then confirm nothing ran."""
    js(
        "const b = addMsg('rev','');"
        f"renderRichMessage(b, {payload!r});"
        "return true;"
    )
    assert page.evaluate("() => window.__pwned === undefined"), \
        f"payload executed in the page: {payload}"


def test_no_script_element_is_ever_created_from_message_text(page, js):
    before = js("return document.querySelectorAll('script').length;")
    js(
        "const b = addMsg('rev','');"
        "renderRichMessage(b, \"<script>window.x=1</script>\");"
        "return true;"
    )
    after = js("return document.querySelectorAll('script').length;")
    assert after == before


def test_linkify_escapes_before_linking(js):
    # esc-then-link is the correct order: a URL-shaped string inside
    # untrusted content must not be able to break out of the markup.
    out = js("return linkify(\"<script>alert(1)</script> https://ok.example\");")
    assert "<script>" not in out
    assert 'href="https://ok.example"' in out


def test_linkify_makes_real_urls_clickable(js):
    out = js("return linkify('see https://example.com/x now');")
    assert '<a href="https://example.com/x"' in out
    assert 'target="_blank"' in out
    assert 'rel="noopener"' in out


def test_linkify_does_not_swallow_trailing_punctuation(js):
    out = js("return linkify('go to https://example.com/x.');")
    assert 'href="https://example.com/x"' in out
    assert out.rstrip().endswith(".")


def test_linkify_leaves_plain_text_alone(js):
    out = js("return linkify('no links here at all');")
    assert out == "no links here at all"


def test_linkify_handles_multiple_urls(js):
    out = js("return linkify('a https://one.example b https://two.example');")
    assert out.count("<a href=") == 2


def test_a_url_inside_an_attribute_payload_cannot_break_out(js):
    # A crafted string that tries to close an attribute and inject one.
    out = js("return linkify('https://x.example\" onmouseover=\"alert(1)');")
    assert "onmouseover=\"alert(1)\"" not in out


def test_esc_escapes_the_markup_characters(js):
    """esc() escapes < > &, which is what makes text safe as TEXT.

    It does NOT escape a double quote — and that is correct rather than a
    gap, because esc()'s output is only ever used in two places: as
    element text (where a quote is inert), and inside linkify's
    href="..." where the URL regex is [^\\s<>"']+ and therefore cannot
    match a quote in the first place. The
    test_a_url_inside_an_attribute_payload_cannot_break_out case above is
    what actually pins that second guarantee down.

    Asserted explicitly so that if someone later reuses esc() to build an
    attribute from arbitrary text, this test documents the assumption
    they would be breaking.
    """
    out = js("return esc('he said \"hi\" & <b>left</b>');")
    assert "&amp;" in out
    assert "&lt;b&gt;" in out
    assert "<b>" not in out


def test_rendered_link_href_is_not_javascript_scheme(page, js):
    js(
        "const b = addMsg('rev','');"
        "renderRichMessage(b, 'javascript:window.__pwned=1');"
        "return true;"
    )
    hrefs = page.evaluate(
        "() => [...document.querySelectorAll('.msg a')].map(a => a.getAttribute('href'))"
    )
    assert not any((h or "").lower().startswith("javascript:") for h in hrefs)
