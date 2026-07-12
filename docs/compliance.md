# Compliance & ethical design

A functional outbound system without a compliance posture signals that the
builder doesn't understand the risk. This is the short version of the master
plan's §21, scoped to what Revenant actually enforces.

## Principles in force

- **Public data only.** Reconnaissance touches job posts, careers pages, public
  GitHub, status pages, engineering blogs, and news. No email harvesters, no
  private repos, no breach-data brokers. If the target hasn't published it,
  Revenant doesn't know it.
- **Show, don't tell — honestly.** Every claim on a microsite cites a *verbatim*
  evidence excerpt (`ghost/models.py::Evidence`). If a prospect asks "how did you
  know that," we point at the exact source, not a paraphrase.
- **Human in the loop by default.** Nothing sends without a click in the console.
  `DRY_RUN=1` is the default; real sends are gated to team-owned inboxes during
  the buildathon.
- **Suppression is first-class.** Unsubscribe / "not relevant" / angry replies →
  permanent or timed suppression. A won deal is never re-pinged.
- **Voice consent.** ElevenLabs voices are limited to ones we own or have written
  consent for. Cloning a prospect, executive, or public figure is not a feature
  and never will be.
- **Budget is a guardrail, not a suggestion.** The signal gate kills boilerplate
  before spend; the gate's routing cannot be overridden to force a killed lead
  through the pipeline.

## What we log per touch (design)

`legal_basis` (consent | legitimate_interest | contractual | none),
`recipient_jurisdiction`, and a computed `can_send` the delivery layer checks
last. Every email carries a physical address footer and a one-click unsubscribe.

## Regulations designed against

CAN-SPAM (US), GDPR (EU), UK GDPR + PECR, India DPDP Act 2023, CASL (Canada —
suppressed by default as the strictest). LinkedIn automation is **not** a feature
in any version — assist mode only.
