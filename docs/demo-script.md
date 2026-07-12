# Revenant — 3-minute demo script

The demo follows the storyline's five acts. Every beat maps to something live on
screen. Rehearse until the narration and the dashboard move together.

**Pre-flight:** `make demo` (runs the loop, publishes to the console). Have the
console at `localhost:5175` and one microsite open in a second tab.

---

## Act I — The hook (20s)

> "SDR agencies charge **fifteen thousand dollars a month** to send emails people
> delete. We built the agency that ships **working software** instead."

Open Hermes on Telegram. Dictate the seller identity with **Wispr Flow**:
> *"We sell EchoDesk AI — voice AI that answers every patient call for busy clinics."*

Hermes fires `ghost-onboard` → `ghost-hunt`.

## Act II — Autonomous reconnaissance (35s)

Switch to the **console**. As it fills:

- **Linkup** surfaced a clinic chain with 4 open front-desk roles and an 8-minute
  hold time. Point at the **verbatim evidence** panel — *their own words*, cited.
- One lead (Cloudleaf) turns **red / killed**: *"Boilerplate posting — the gate
  rejected it for a tenth of a cent, before we spent a token."*
- One (BrightSmile) is **amber / warm** — real but thin, queued as a soft touch.
- Meridian is **promoted**. "Watch what it does with a real signal."

## Act III — Just-in-time engineering (35s)

Open Meridian's **live microsite**. Scroll it:
- Their name, their pain quoted verbatim with a **"— per your careers page"** cite.
- The **working prototype**: an AI receptionist. *Talk to it on stage* (live
  ElevenLabs convo agent in live mode).

## Act IV — The AI-made walkthrough (50s) ⭐

> "A cold link isn't enough. So an agent recorded a walkthrough of the thing it
> just built — narration, screen capture, and all. **No human touched this.**"

Play the walkthrough. The presenter bubble pulses; the narration explains what
was built and why it fits *this clinic*, in a voice tuned to the exec's vibe.

## Act V — The conversion loop (40s)

Back in the console:
- The **outreach email** carries the video + the live link. Click **Approve & Send**
  → it lands in a (team-owned) inbox. `DRY_RUN` keeps it honest.
- On the microsite, click **Book a paid pilot — ₹X**. Pay ₹1 in Razorpay test mode.
- The **Razorpay webhook** flips the deal to **WON** live on the dashboard, and
  Hermes pings the human closer on Telegram.

> "Every agent, every video, every follow-up, every rupee — one autonomous loop.
> Built on **Hermes**."

---

## Fallbacks (if the venue wifi dies)

- Whole pipeline runs **offline** (`REVENANT_MODE=offline`) — recon, gate,
  build, walkthrough storyboard, all from fixtures.
- Pre-rendered walkthrough MP4 + a screen-recorded backup of the full demo.
- The console reads `out/ledger.json`, no Convex needed.

## Cut order (if short on time)

HeyGen avatar → live second-seller onboarding → real email send (show DRY_RUN
outbox) → follow-up cron (describe + show code). **Never cut:** microsite,
walkthrough, approve, Razorpay flip.
