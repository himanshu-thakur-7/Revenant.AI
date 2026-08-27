"""T0 coverage for the four evals/checks/html_.py checks that work purely
from a local file (url="" falls through to the on-disk copy via
_fetch_html) -- element_id_contract, no_external_img, demo_input_prefilled,
specificity_lint. renders_clean genuinely needs a live URL + a real
browser and stays untested here by design (see its own docstring).
Previously zero test coverage for this whole module.
"""

from __future__ import annotations

from evals.checks.html_ import (
    demo_input_prefilled,
    element_id_contract,
    no_external_img,
    specificity_lint,
)

_GOOD_IDS = '<div id="demo"><input id="demoInput"><button id="demoRun">' \
           '<pre id="demoOutput"></pre><code id="code"></code><a id="cta"></a></div>'


def _write(tmp_path, html, name="p.html"):
    p = tmp_path / name
    p.write_text(html, encoding="utf-8")
    return str(p)


def test_element_id_contract_all_present_passes(tmp_path):
    c = element_id_contract("", _write(tmp_path, f"<html><body>{_GOOD_IDS}</body></html>"))
    assert c.passed


def test_element_id_contract_missing_ones_fails(tmp_path):
    c = element_id_contract("", _write(tmp_path, "<html><body><div id='demo'></div></body></html>"))
    assert not c.passed
    assert "demoInput" in str(c.measured)


def test_element_id_contract_no_file_fails(tmp_path):
    c = element_id_contract("", str(tmp_path / "nope.html"))
    assert not c.passed


def test_no_external_img_clean_passes(tmp_path):
    html = "<html><body><img src='local.png'></body></html>"
    c = no_external_img("", _write(tmp_path, html))
    assert c.passed


def test_no_external_img_flags_remote_src(tmp_path):
    html = "<html><body><img src='https://cdn.example.com/x.png'></body></html>"
    c = no_external_img("", _write(tmp_path, html))
    assert not c.passed
    assert c.measured == 1


def test_demo_input_prefilled_enough_content_passes(tmp_path):
    html = ('<html><body><textarea id="demoInput">' +
           "x" * 50 + "</textarea></body></html>")
    c = demo_input_prefilled("", _write(tmp_path, html))
    assert c.passed


def test_demo_input_prefilled_empty_fails(tmp_path):
    html = '<html><body><textarea id="demoInput"></textarea></body></html>'
    c = demo_input_prefilled("", _write(tmp_path, html))
    assert not c.passed


def test_demo_input_prefilled_via_value_attr(tmp_path):
    html = f'<html><body><input id="demoInput" value="{"y" * 50}"></body></html>'
    c = demo_input_prefilled("", _write(tmp_path, html))
    assert c.passed


def test_specificity_lint_generic_html_fails(tmp_path):
    # No prospect-specific clues anywhere, no company name mentions.
    html = "<html><body><h1>Welcome to our platform</h1></body></html>"
    c = specificity_lint("", _write(tmp_path, html), merchant="Acme Corp",
                         pain="checkout abandonment issues")
    assert not c.passed


def test_specificity_lint_no_html_available_fails(tmp_path):
    c = specificity_lint("", str(tmp_path / "nope.html"), merchant="Acme")
    assert not c.passed
