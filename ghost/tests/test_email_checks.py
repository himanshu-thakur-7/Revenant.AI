"""T0 coverage for evals/checks/email_.py::no_truncated_signoff — pure
local-file logic, no network, so unlike most of evals/checks/* (which hit
real URLs by design) this one belongs in the offline suite. Regression
guard for a real, twice-recurring bug this session: two separate live
campaigns (Meesho, PhonePe) both shipped an email whose body ends with the
single bare word "the" — see evals/checks/email_.py's own docstring.
"""

from __future__ import annotations

from evals.checks.email_ import no_truncated_signoff


def _write(tmp_path, body):
    p = tmp_path / "email.md"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_flags_the_actual_live_caught_bug(tmp_path):
    # Real content, trimmed, from out/drafts/phonepe/phonepe-email.md.
    body = (
        "**Subject:** PhonePe's UPI reconciliation streamlined in a day\n\n"
        "Hi there,\n\n"
        "15 min this week to see the live reconciliation on a sample of your data?\n\n"
        "the\n"
    )
    c = no_truncated_signoff(_write(tmp_path, body))
    assert not c.passed


def test_clean_real_sign_off_first_name_passes(tmp_path):
    body = "Hi there,\n\nSome real pitch content here.\n\n15 min this week?\n\nAlex\n"
    c = no_truncated_signoff(_write(tmp_path, body))
    assert c.passed


def test_clean_dash_name_signoff_passes(tmp_path):
    body = "Hi there,\n\nSome real pitch content here.\n\n15 min this week?\n\n— Sam\n"
    c = no_truncated_signoff(_write(tmp_path, body))
    assert c.passed


def test_a_real_sentence_containing_a_stopword_is_not_flagged(tmp_path):
    # "the" appearing INSIDE a real closing sentence must never trip this --
    # only a lone bare stopword as the entire last line does.
    body = "Hi there,\n\nSome pitch content.\n\nThanks for reading the update.\n"
    c = no_truncated_signoff(_write(tmp_path, body))
    assert c.passed


def test_missing_file_fails_cleanly():
    c = no_truncated_signoff("")
    assert not c.passed


def test_empty_file_fails_cleanly(tmp_path):
    c = no_truncated_signoff(_write(tmp_path, "   \n\n  \n"))
    assert not c.passed
