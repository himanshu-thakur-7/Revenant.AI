"""Tool registry — the @tool decorator and JSON-schema derivation.

An agent's tools are just Python functions. The decorator collects metadata
(name, description, parameter schema) so the LLM sees them as OpenAI-format
function tools. There are no runtime frameworks and no magic — a tool is a
callable plus a schema, and the base class does the wiring.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Union, get_args, get_origin, get_type_hints


# ── type-hint → JSON schema ────────────────────────────────────
_PRIMITIVE = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _hint_to_schema(hint: Any) -> dict[str, Any]:
    """Best-effort JSON-schema for a Python type hint."""
    if hint in _PRIMITIVE:
        return {"type": _PRIMITIVE[hint]}

    origin = get_origin(hint)
    args = get_args(hint)

    # Optional[X] / X | None  →  the non-None branch
    if origin is Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _hint_to_schema(non_none[0])
        return {}

    if origin in (list, tuple):
        item = args[0] if args else str
        return {"type": "array", "items": _hint_to_schema(item)}

    if origin is dict:
        return {"type": "object"}

    return {"type": "string"}  # fall back — the LLM will send a string


# ── Tool dataclass ─────────────────────────────────────────────
@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    parameters: dict[str, Any]
    required: list[str] = field(default_factory=list)

    def schema(self) -> dict[str, Any]:
        """The tool schema in OpenAI/Nous function-tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }

    def call(self, raw_args: str) -> str:
        """Invoke the tool with the LLM's stringified JSON arguments.

        Nous Hermes-4 occasionally double-encodes arguments (returns a JSON
        string whose value is itself a JSON object). We unwrap up to two
        layers before giving up — cheap insurance for a real live-LLM quirk.
        """
        try:
            parsed = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            return f"[tool-error] invalid JSON arguments: {raw_args!r}"

        # Unwrap double-encoding: json.loads returned a string that itself
        # looks like a JSON object.
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                pass

        if not isinstance(parsed, dict):
            return (
                f"[tool-error] {self.name}: expected a JSON object of arguments, "
                f"got {type(parsed).__name__} — {parsed!r}"
            )

        try:
            result = self.fn(**parsed)
        except TypeError as exc:
            return f"[tool-error] {self.name}: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            return f"[tool-error] {self.name} raised {exc.__class__.__name__}: {exc}"

        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, default=str)
        except (TypeError, ValueError):
            return str(result)


def tool(description: str) -> Callable[[Callable[..., Any]], Tool]:
    """Decorate a Python function → a :class:`Tool`.

    The parameter schema is derived from type hints; required params are those
    without a default. The docstring is not read — pass ``description`` so the
    LLM sees the same thing you meant.
    """

    def deco(fn: Callable[..., Any]) -> Tool:
        hints = get_type_hints(fn)
        sig = inspect.signature(fn)
        params: dict[str, Any] = {}
        required: list[str] = []
        for name, p in sig.parameters.items():
            hint = hints.get(name, str)
            schema = _hint_to_schema(hint)
            # append a param-level description from the function's __doc__?
            # keep it terse for now — an LLM reads the tool description above.
            params[name] = schema
            if p.default is inspect.Parameter.empty:
                required.append(name)

        return Tool(
            name=fn.__name__,
            description=description.strip(),
            fn=fn,
            parameters=params,
            required=required,
        )

    return deco


class ToolRegistry:
    """Ordered mapping of name → Tool, used by an :class:`Agent`."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.add(t)

    def add(self, t: Tool) -> None:
        self._tools[t.name] = t

    def extend(self, tools: list[Tool]) -> None:
        for t in tools:
            self.add(t)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)
