# Revamp Notes

## 2026-07-12 14:00 IST - Web console campaign reliability

- Hardened `website/console.html` only; Telegram flow was intentionally left untouched.
- Added endpoint recovery for the web UI: the console probes the last-good Hermes URL, configured URL, default tunnel, and localhost fallbacks before starting a run.
- Added automatic retry for transient Hermes failures such as reconnect requests, dropped event streams, gateway errors, 502/503/504 responses, and model-generated "service temporarily down" recovery messages.
- Preserved the user's original request after failure and added one-click `Retry last turn` and `Reconnect endpoint` chips.
- Added per-turn observability: tool calls, agents, elapsed time, status, and estimated spend now reset each run and the trace logs retry attempts plus estimated spend on completion.
- Added defensive event-stream checks so failed `/events` responses surface as traceable errors instead of leaving the UI spinning.
