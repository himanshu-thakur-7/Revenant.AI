"""Conversation memory — messages, tool calls, and light token trimming.

The list mirrors the OpenAI/Nous chat format so it can be fed straight into
``chat.completions.create``. A crude character-based truncator drops the
oldest non-system messages when the buffer grows unreasonably large.
"""

from __future__ import annotations

from typing import Any


# Rough char budget before we start trimming. Nous Hermes-4-405B has a
# 128k-token context; we stay well under it — the goal is a snappy REPL, not
# a marathon session.
_SOFT_CHAR_BUDGET = 240_000


class ConversationMemory:
    def __init__(self, system: str) -> None:
        self._system: dict[str, Any] = {"role": "system", "content": system}
        self._msgs: list[dict[str, Any]] = []

    # ── adds ──────────────────────────────────────────────────
    def add_user(self, text: str) -> None:
        self._msgs.append({"role": "user", "content": text})
        self._trim()

    def add_assistant(self, text: str) -> None:
        self._msgs.append({"role": "assistant", "content": text})
        self._trim()

    def add_assistant_tool_calls(self, content: str | None, tool_calls: list[Any]) -> None:
        """Append the assistant turn that requested tools."""
        self._msgs.append({
            "role": "assistant",
            "content": content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self._msgs.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        })
        self._trim()

    # ── reads ─────────────────────────────────────────────────
    def messages(self) -> list[dict[str, Any]]:
        return [self._system, *self._msgs]

    def set_system(self, text: str) -> None:
        self._system = {"role": "system", "content": text}

    def reset(self) -> None:
        self._msgs.clear()

    def __len__(self) -> int:
        return len(self._msgs)

    # ── housekeeping ──────────────────────────────────────────
    def _trim(self) -> None:
        while self._char_size() > _SOFT_CHAR_BUDGET and len(self._msgs) > 4:
            # Drop the oldest user/assistant pair. Never drop system.
            self._msgs.pop(0)

    def _char_size(self) -> int:
        total = len(str(self._system.get("content", "")))
        for m in self._msgs:
            total += len(str(m.get("content", "")))
            for tc in m.get("tool_calls", []) or []:
                total += len(str(tc.get("function", {}).get("arguments", "")))
        return total
