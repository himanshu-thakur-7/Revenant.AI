"""Thin Telegram Bot API client — just the methods the gateway needs.

Long-polling (``getUpdates``) so no public webhook URL is required — the bot
runs from the founder's laptop behind any venue wifi. All calls are
synchronous httpx; the bot loop is single-threaded and that's plenty for one
founder driving one chat.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class TelegramAPI:
    def __init__(self, token: str, *, timeout: float = 65.0) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = httpx.Client(timeout=timeout)

    # ── receive ───────────────────────────────────────────────
    def get_updates(self, offset: int | None = None,
                    timeout: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout,
                                  "allowed_updates": '["message","callback_query"]'}
        if offset is not None:
            params["offset"] = offset
        try:
            r = self._client.get(f"{self._base}/getUpdates", params=params,
                                 timeout=timeout + 10)
            data = r.json()
        except httpx.HTTPError:
            return []
        return data.get("result", []) if data.get("ok") else []

    # ── send ──────────────────────────────────────────────────
    def send_message(self, chat_id: int, text: str, *,
                     reply_markup: dict | None = None,
                     parse_mode: str | None = "HTML",
                     disable_preview: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096]}
        if parse_mode:
            body["parse_mode"] = parse_mode
        if reply_markup:
            body["reply_markup"] = reply_markup
        if disable_preview:
            body["link_preview_options"] = {"is_disabled": True}
        return self._post("sendMessage", body)

    def edit_message(self, chat_id: int, message_id: int, text: str, *,
                     reply_markup: dict | None = None,
                     parse_mode: str | None = "HTML") -> dict[str, Any]:
        body: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id,
                                "text": text[:4096]}
        if parse_mode:
            body["parse_mode"] = parse_mode
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        return self._post("editMessageText", body)

    def send_video(self, chat_id: int, path: str | Path, *,
                   caption: str = "") -> dict[str, Any]:
        p = Path(path)
        if not p.exists():
            return {"ok": False, "error": f"video missing: {p}"}
        with open(p, "rb") as f:
            files = {"video": (p.name, f, "video/mp4")}
            data = {"chat_id": str(chat_id), "caption": caption[:1024],
                    "parse_mode": "HTML", "supports_streaming": "true"}
            return self._post_multipart("sendVideo", data, files)

    def send_document(self, chat_id: int, path: str | Path, *,
                      caption: str = "") -> dict[str, Any]:
        p = Path(path)
        if not p.exists():
            return {"ok": False, "error": f"document missing: {p}"}
        with open(p, "rb") as f:
            files = {"document": (p.name, f, "application/octet-stream")}
            data = {"chat_id": str(chat_id), "caption": caption[:1024],
                    "parse_mode": "HTML"}
            return self._post_multipart("sendDocument", data, files)

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        self._post("sendChatAction", {"chat_id": chat_id, "action": action})

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._post("answerCallbackQuery",
                   {"callback_query_id": callback_id, "text": text[:200]})

    # ── plumbing ──────────────────────────────────────────────
    def _post(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._client.post(f"{self._base}/{method}", json=body)
            return r.json()
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc)}

    def _post_multipart(self, method: str, data: dict, files: dict) -> dict[str, Any]:
        try:
            r = self._client.post(f"{self._base}/{method}", data=data,
                                  files=files, timeout=120)
            return r.json()
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc)}


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    """Build an inline keyboard. `rows` is a list of button rows; each button
    is (label, callback_data)."""
    return {"inline_keyboard": [
        [{"text": label, "callback_data": data} for label, data in row]
        for row in rows
    ]}
