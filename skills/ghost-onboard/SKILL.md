---
name: ghost-onboard
description: >
  Turn a founder's spoken/typed company description into a structured Revenant
  seller profile (ICP, pain keywords, prototype kind), then optionally kick off a
  hunt. Use when a new founder describes their company for the first time, or when
  the demo pivots to a brand-new seller live on stage. Pairs with Wispr Flow: the
  founder dictates, this skill structures.
metadata:
  type: agent-skill
  runtime: python
---

# ghost-onboard

The front door. A founder describes their company in one or two sentences (via
Wispr Flow dictation into the Hermes Telegram gateway, or typed). This skill
extracts a `SellerProfile` — the single config that re-points the entire pipeline
at a new vertical.

## When to use

- First contact with a new seller.
- On stage: "watch us onboard a company we've never seen" (the configurability proof).

## How to run

```bash
ghost onboard "{{dictated_blurb}}" --slug {{slug|default:custom}} --limit {{limit|default:3}}
```

This structures the profile *and* immediately runs the hunt, so the founder sees
results from a single sentence.

## What the founder said → what Revenant extracts

Given: *"We sell an AI receptionist for dental clinics drowning in front-desk calls."*

Revenant derives:
- **ICP**: multi-location dental groups with high call volume
- **pain_keywords**: front desk call volume, patient wait time, receptionist hiring, …
- **prototype_kind**: `voice_demo` (an embedded conversational agent)

## Then

Hand off to `ghost-hunt` narration. The profile is stored in the ledger, so
subsequent hunts for the same seller reuse it.
