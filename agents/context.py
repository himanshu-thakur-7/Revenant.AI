"""FounderContext — ingests the founder's startup docs + code.

Two paths, unified afterwards:

* **Local folder** — walk the tree, skip vendor/build dirs, load text files up
  to a size budget. Simplest, works offline.
* **GitHub repo URL** — shallow-clone into a tmpdir, then treat as a folder.

After ingestion the context exposes:

* :meth:`file_map` — {relpath: content} — every text file we kept
* :meth:`tree` — a printable outline of what was loaded
* :meth:`search` — plain substring/regex search across all files
* :meth:`summary` — a one-shot LLM-generated pitch of the startup, cached

The Orchestrator agent gets tools that call these methods; nothing else in the
codebase reads the founder's repo directly.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ghost.llm import complete


# Files we always skip — vendor, build output, secrets, binaries.
_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", ".next", ".nuxt", ".turbo",
    "target", ".cache", "coverage", ".idea", ".vscode", "out",
}

# Extensions we treat as ingestible text. Everything else is skipped so the
# LLM never sees a binary blob.
_TEXT_EXT = {
    ".md", ".txt", ".rst", ".mdx",
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".swift", ".rb", ".php",
    ".c", ".h", ".cpp", ".hpp", ".cs",
    ".sql", ".sh", ".bash", ".zsh", ".fish",
    ".yml", ".yaml", ".toml", ".json", ".env.example", ".cfg", ".ini",
    ".html", ".css", ".scss", ".sass",
    ".proto", ".graphql", ".sol",
    ".dockerfile", ".makefile", "",  # extensionless = Makefile, Dockerfile, LICENSE, etc.
}

# Files we prioritise when the budget is tight.
_PRIORITY_NAMES = {
    "README.md", "README.rst", "README", "README.txt",
    "ARCHITECTURE.md", "OVERVIEW.md", "CLAUDE.md", "AGENTS.md",
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
    "Dockerfile", "docker-compose.yml", "Makefile",
}

# Per-file and total ingestion caps — keep the LLM context reasonable.
_MAX_FILE_BYTES = 60_000
_MAX_TOTAL_BYTES = 400_000


@dataclass
class FounderContext:
    source: str                              # human label, e.g. "~/mycompany" or the URL
    root: Path                               # the resolved local root
    files: dict[str, str] = field(default_factory=dict)  # relpath → content
    _tmp: str | None = None                  # cleanup handle for cloned repos
    _summary_cache: str | None = None

    @property
    def product_name(self) -> str:
        """Best-effort product/company name derived from the INGESTED repo, so
        Revenant sells for whatever startup was set up — not a hardcoded name.
        Prefers a clean README H1, falls back to the repo/folder slug."""
        for p, body in self.files.items():
            if Path(p).name.lower().startswith("readme"):
                m = re.search(r"^\s{0,3}#\s+(.+)$", body, re.M)
                if m:
                    raw = m.group(1)
                    # strip badges/emoji/taglines after a separator
                    raw = re.split(r"[—:|–]|\s-\s", raw)[0]
                    raw = re.sub(r"[*_`#\[\]()!]|<[^>]+>|https?://\S+", "", raw)
                    name = " ".join(raw.split()).strip()
                    if 1 < len(name) <= 40 and not name.lower().startswith(("welcome", "the ")):
                        return name
                break
        slug = self.source.rstrip("/").split("/")[-1].replace(".git", "")
        return slug.replace("-", " ").replace("_", " ").title() if slug else "our product"

    # ── ingestion ─────────────────────────────────────────────
    @classmethod
    def from_folder(cls, path: str | Path) -> "FounderContext":
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"not a folder: {root}")
        ctx = cls(source=str(root), root=root)
        ctx._ingest()
        return ctx

    @classmethod
    def from_github(cls, url_or_slug: str) -> "FounderContext":
        """Shallow-clone into a tmpdir and ingest."""
        url = _normalise_github(url_or_slug)
        tmp = tempfile.mkdtemp(prefix="revenant-ctx-")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", url, tmp],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(
                f"git clone failed for {url}: {exc.stderr.strip() or exc}"
            ) from exc
        ctx = cls(source=url, root=Path(tmp), _tmp=tmp)
        ctx._ingest()
        return ctx

    def cleanup(self) -> None:
        if self._tmp and os.path.isdir(self._tmp):
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    # ── the walk ──────────────────────────────────────────────
    def _ingest(self) -> None:
        candidates: list[tuple[int, Path]] = []
        for p in _walk_text_files(self.root):
            rel = p.relative_to(self.root).as_posix()
            score = 0
            if p.name in _PRIORITY_NAMES:
                score -= 100  # bubble to the top
            score += len(rel.split("/"))  # prefer shallow files
            candidates.append((score, p))

        candidates.sort()  # lowest score first (README before deep source)
        total = 0
        for _score, p in candidates:
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if len(data) > _MAX_FILE_BYTES:
                data = data[:_MAX_FILE_BYTES]
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue  # binary-in-disguise
            if total + len(text) > _MAX_TOTAL_BYTES:
                text = text[: max(0, _MAX_TOTAL_BYTES - total)]
                if not text:
                    break
            rel = p.relative_to(self.root).as_posix()
            self.files[rel] = text
            total += len(text)

    # ── reads for tools ───────────────────────────────────────
    def file_map(self) -> dict[str, str]:
        return dict(self.files)

    def paths(self) -> list[str]:
        return sorted(self.files.keys())

    def read(self, path: str) -> str | None:
        # exact match first, then case-insensitive suffix
        if path in self.files:
            return self.files[path]
        low = path.lower().lstrip("/")
        for k, v in self.files.items():
            if k.lower() == low or k.lower().endswith("/" + low):
                return v
        return None

    def search(self, pattern: str, max_hits: int = 40) -> list[dict[str, str]]:
        """Regex search across every ingested file. Returns line hits."""
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            rx = re.compile(re.escape(pattern), re.IGNORECASE)
        hits: list[dict[str, str]] = []
        for path, content in self.files.items():
            for i, line in enumerate(content.splitlines(), 1):
                if rx.search(line):
                    hits.append({"path": path, "line": str(i), "text": line.strip()[:200]})
                    if len(hits) >= max_hits:
                        return hits
        return hits

    def tree(self, limit: int = 60) -> str:
        paths = self.paths()
        if len(paths) <= limit:
            return "\n".join(paths)
        return "\n".join(paths[:limit]) + f"\n… and {len(paths) - limit} more"

    # ── LLM summary ───────────────────────────────────────────
    def summary(self) -> str:
        """One-shot LLM-generated pitch — cached. Used in the system prompt."""
        if self._summary_cache is not None:
            return self._summary_cache

        # Feed the LLM the most-signal files first: README + first ~40k of code.
        prio_names = [p for p in self.paths() if Path(p).name in _PRIORITY_NAMES]
        others = [p for p in self.paths() if p not in prio_names]
        chosen = prio_names + others
        blob: list[str] = []
        budget = 30_000
        for path in chosen:
            body = self.files[path]
            snippet = body[: min(4000, budget)]
            blob.append(f"### {path}\n{snippet}")
            budget -= len(snippet)
            if budget <= 0:
                break

        prompt = (
            "You are being briefed on a software startup so a downstream sales agent can "
            "represent it accurately. Read the files below and produce a compact briefing "
            "for internal use.\n\n"
            "OUTPUT (Markdown, ≤ 350 words):\n"
            "1. **One-liner** — what the product does, in a single sentence.\n"
            "2. **What it actually is** — core capabilities, tech stack, deployment model.\n"
            "3. **ICP (ideal customer profile)** — the type of company this fits, with 3-5 "
            "concrete pain signals a prospect would leak on their careers page / status page.\n"
            "4. **The pitch angles** — 3 angles the sales agent should lead with.\n\n"
            "Ground every claim in the files. Do not invent features not present in the code.\n\n"
            "FILES:\n" + "\n\n".join(blob)
        )
        offline_stub = (
            f"### Startup briefing (offline stub for {self.source})\n"
            f"- {len(self.files)} files ingested; live LLM disabled.\n"
            f"- Run with `REVENANT_MODE=live` and an `LLM_API_KEY` for a real summary."
        )
        self._summary_cache = complete(
            prompt,
            agent="orchestrator.summary",
            system="You are a terse principal engineer briefing a colleague.",
            offline=offline_stub,
            temperature=0.2,
        ).strip()
        return self._summary_cache


# ── helpers ────────────────────────────────────────────────────
def _walk_text_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith(".") and fn not in {".env.example"}:
                continue
            p = Path(dirpath) / fn
            ext = p.suffix.lower()
            if ext in _TEXT_EXT or fn in _PRIORITY_NAMES or fn.lower() in {"dockerfile", "makefile"}:
                yield p


def _normalise_github(spec: str) -> str:
    """Accept ``owner/repo``, ``github.com/owner/repo``, or a full URL."""
    s = spec.strip()
    if s.startswith(("http://", "https://", "git@")):
        return s
    if s.startswith("github.com/"):
        return f"https://{s}"
    if re.fullmatch(r"[\w.-]+/[\w.-]+", s):
        return f"https://github.com/{s}.git"
    return s
