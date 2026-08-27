"""evals/checks/email_.py — the last gate before a founder sends
something to a real named human at a real company.

A bad prototype wastes compute. A bad email burns the prospect: it is
the artifact that actually leaves the building, and it cannot be
un-sent. These checks are what stand between a generic mail-merge and
the founder's reputation.

Offline: everything reads local files; url_alive is stubbed where used.
"""

from __future__ import annotations

import pytest

import evals.checks.email_ as email_
from evals.checks import Check


@pytest.fixture
def md(tmp_path):
    def _write(body):
        p = tmp_path / "email.md"
        p.write_text(body, encoding="utf-8")
        return str(p)
    return _write


# Shaped and sized like a real draft (~1.3KB on disk, matching the ones in
# out/drafts/) — a shorter fixture would trip the length floor and make
# every other assertion in this file test the wrong thing.
REAL = """# Draft — PhonePe

- **To:** Dan Zhou · Head of Payments, PhonePe
- **State:** awaiting_review
- **Prototype:** https://x.test/phonepe/
- **Deck:** https://x.test/phonepe-pitch.pptx
- **Cost so far:** $0.9760

---

**Subject:** PhonePe's UPI reconciliation, solved in a day

Hi Dan,

I noticed PhonePe's UPI settlement reconciliation friction — the manual
matching step between settlement files and the ledger before payouts can
be released. At your volume that is a standing daily cost, not an
occasional one.

Razorpay automates that reconciliation end to end: settlement files are
parsed on arrival, matched against the ledger, and exceptions are surfaced
as a short queue instead of a spreadsheet. The pieces that usually need a
human are the exceptions, not the matches.

Instead of pitching, I built you a working prototype on your kind of data.
Prototype: https://x.test/phonepe/

15 min this week to see the live reconciliation on a sample of your data?

Alex
"""


# ── file presence ─────────────────────────────────────────────────────

def test_a_real_draft_passes(md):
    assert email_.md_exists_nonempty(md(REAL)).passed


def test_a_missing_file_fails(md):
    c = email_.md_exists_nonempty("/nonexistent/email.md")
    assert not c.passed and "no file" in c.detail


def test_an_empty_path_fails(md):
    assert not email_.md_exists_nonempty("").passed


def test_a_stub_length_draft_fails(md):
    # A 50-char "email" means the Sales step bailed early.
    c = email_.md_exists_nonempty(md("too short"))
    assert not c.passed
    assert c.measured < 400


# ── subject line ──────────────────────────────────────────────────────

def test_a_normal_subject_passes():
    assert email_.subject_len_ok("PhonePe's UPI reconciliation, solved").passed


def test_an_empty_subject_fails():
    # Caught live on a real campaign: a blank subject ships as "(no subject)".
    assert not email_.subject_len_ok("").passed


def test_an_overlong_subject_fails():
    # Past ~60 chars mail clients truncate mid-word in the inbox list.
    assert not email_.subject_len_ok("x" * 90).passed


def test_the_subject_boundary_is_inclusive():
    assert email_.subject_len_ok("x" * 60).passed
    assert not email_.subject_len_ok("x" * 61).passed


# ── banned openers ────────────────────────────────────────────────────

@pytest.mark.parametrize("opener", [
    "Quick question about your stack",
    "I'm reaching out because",
    "Just circling back on this",
    "Hope this finds you well",
    "Touching base about payments",
    "I wanted to share something",
])
def test_template_openers_are_rejected(md, opener):
    # These are the phrases every SDR uses; their presence is the single
    # clearest signal the email reads as automated.
    c = email_.no_banned_openers(md(f"**Subject:** x\n\n{opener}, ...\n"))
    assert not c.passed
    assert c.measured


def test_detection_is_case_insensitive(md):
    assert not email_.no_banned_openers(md("QUICK QUESTION for you")).passed


def test_a_genuine_opener_passes(md):
    assert email_.no_banned_openers(md(REAL)).passed


def test_banned_opener_check_needs_a_file():
    assert not email_.no_banned_openers("/nonexistent/x.md").passed


# ── placeholders that would ship as-is ────────────────────────────────

@pytest.mark.parametrize("ph", ["[Name]", "{{first_name}}", "<recipient>", "[COMPANY]", "<company>"])
def test_unfilled_placeholders_are_caught(md, ph):
    # Shipping "Hi [Name]," to a real prospect is the single most
    # embarrassing possible failure of this product.
    c = email_.no_placeholder_name(md(f"Hi {ph},\n\nreal body here.\n"))
    assert not c.passed


def test_a_filled_draft_has_no_placeholders(md):
    assert email_.no_placeholder_name(md(REAL)).passed


# ── evidence grounding ────────────────────────────────────────────────

def test_an_email_citing_real_clues_passes(md):
    c = email_.evidence_grounding(
        md(REAL), merchant="PhonePe", merchant_domain="phonepe.com",
        pain="UPI settlement reconciliation friction",
        contact_name="Dan", contact_title="Head of Payments")
    assert c.passed


def test_a_generic_email_fails_grounding(md):
    # Mentions the company and nothing else — the "logo-swappable" email.
    body = "**Subject:** hello\n\nHi there,\n\nPhonePe should use our platform.\n"
    c = email_.evidence_grounding(
        md(body), merchant="PhonePe", merchant_domain="phonepe.com",
        pain="UPI settlement reconciliation friction")
    assert not c.passed


def test_grounding_needs_a_file():
    assert not email_.evidence_grounding("/nonexistent/x.md", merchant="X").passed


def test_grounding_with_no_clues_available_does_not_crash(md):
    # No merchant, no pain, no contact — nothing to ground against.
    c = email_.evidence_grounding(md(REAL), merchant="", pain="")
    assert isinstance(c, Check)


# ── truncated sign-off (a real, twice-recurring bug) ──────────────────

def test_a_dangling_stopword_signoff_is_caught(md):
    assert not email_.no_truncated_signoff(md(REAL.replace("Alex\n", "the\n"))).passed


def test_a_real_name_signoff_passes(md):
    assert email_.no_truncated_signoff(md(REAL)).passed


# ── artifact links ────────────────────────────────────────────────────

@pytest.fixture
def stub_alive(monkeypatch):
    def _install(ok=True):
        def fake(url, *, name="url_alive", **kw):
            return Check(name, ok, "stubbed")
        monkeypatch.setattr("evals.checks.http_.url_alive", fake)
    return _install


def test_present_and_live_links_pass(md, stub_alive):
    stub_alive(True)
    body = REAL + "\nWalkthrough: https://x.test/w.mp4\n"
    c = email_.links_present_and_alive(
        md(body), "https://x.test/phonepe/", "https://x.test/w.mp4")
    assert c.passed


def test_a_link_missing_from_the_body_fails(md, stub_alive):
    # The artifact exists but the email never references it — the prospect
    # would receive a pitch with nothing to click.
    stub_alive(True)
    c = email_.links_present_and_alive(
        md(REAL), "https://x.test/phonepe/", "https://x.test/NOT-IN-BODY.mp4")
    assert not c.passed
    assert "walkthrough" in c.detail.lower()


def test_a_present_but_dead_link_fails(md, stub_alive):
    # The worst case: the email looks complete and the link 404s for the
    # prospect. This is the whole reason the check re-fetches.
    stub_alive(False)
    c = email_.links_present_and_alive(md(REAL), "https://x.test/phonepe/", "")
    assert not c.passed
    assert "not alive" in c.detail


def test_an_absent_optional_artifact_is_not_a_failure(md, stub_alive):
    # No walkthrough was built yet; that is not the email's fault.
    stub_alive(True)
    assert email_.links_present_and_alive(md(REAL), "https://x.test/phonepe/", "").passed


def test_links_check_needs_a_file(stub_alive):
    stub_alive(True)
    assert not email_.links_present_and_alive("/nonexistent/x.md", "https://x/", "").passed
