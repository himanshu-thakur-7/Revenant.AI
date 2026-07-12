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

from ghost.llm import complete, complete_strong


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
    _extra_tmp: list = field(default_factory=list)  # extra tmp dirs (multi-source)
    source_report: dict = field(default_factory=dict)  # {ok:[...], skipped:[...]}
    _summary_cache: str | None = None

    @property
    def product_name(self) -> str:
        """Best-effort product/company name derived from the INGESTED sources
        (repo README H1, then the website homepage title), so Revenant sells
        for whatever startup was set up. Falls back to a cleaned source slug."""
        def _clean(raw: str) -> str:
            raw = re.split(r"[—:|·–]|\s[-•]\s", raw)[0]
            raw = re.sub(r"[*_`#\[\]()!]|<[^>]+>|https?://\S+", "", raw)
            return " ".join(raw.split()).strip()

        # 1. README H1
        for p, body in self.files.items():
            if Path(p).name.lower().startswith("readme"):
                m = re.search(r"^\s{0,3}#\s+(.+)$", body, re.M)
                if m:
                    name = _clean(m.group(1))
                    if 1 < len(name) <= 40 and not name.lower().startswith(("welcome", "the ")):
                        return name
                break
        # 2. source slug — the repo/domain name is usually the brand
        #    (weaviate.io → Weaviate). Only consider sources that actually
        #    contributed content (skip a private repo we couldn't read).
        ok_sources = (self.source_report or {}).get("ok") or self.source.split(" + ")
        for s in ok_sources:
            slug = s.rstrip("/").split("/")[-1].replace(".git", "")
            slug = re.sub(r"\.(io|com|ai|dev|co|app|net|org|xyz|sh|so)$", "", slug, flags=re.I)
            slug = slug.replace("-", " ").replace("_", " ").strip()
            if slug and slug.lower() not in ("www", "index", "home"):
                return slug.title()
        # 3. website homepage title (last resort — often a tagline)
        home = self.files.get("website/home.md", "")
        m = re.search(r"^#\s+(.+)$", home, re.M) if home else None
        if m:
            name = _clean(m.group(1))
            if 1 < len(name) <= 40:
                return name
        return "our product"

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

    @classmethod
    def from_website(cls, url: str) -> "FounderContext":
        """Build context from a product/company/docs website — homepage plus a
        few key sub-pages (about, product, docs, features, pricing…)."""
        tmp = tempfile.mkdtemp(prefix="revenant-web-")
        ctx = cls(source=url, root=Path(tmp), _tmp=tmp)
        if ctx._ingest_website(url) == 0:
            ctx.cleanup()
            raise RuntimeError(f"couldn't read anything usable from {url}")
        return ctx

    @classmethod
    def from_sources(cls, sources: list[str]) -> "FounderContext":
        """Ingest ANY mix of GitHub repos, product/docs websites, and local
        folders into ONE context. Sources that fail (e.g. a private repo we
        can't clone, or a site that blocks us) are skipped — as long as at
        least one source yields content, we still build a solid understanding.
        Exposes ``self.source_report`` describing what worked / was skipped."""
        sources = [s.strip() for s in dict.fromkeys(s.strip() for s in sources) if s.strip()]
        tmp = tempfile.mkdtemp(prefix="revenant-multi-")
        ctx = cls(source=" + ".join(sources) or "(none)", root=Path(tmp), _tmp=tmp)
        ok: list[str] = []
        skipped: list[str] = []
        for s in sources:
            before = len(ctx.files)
            try:
                if _is_github(s):
                    ctx._ingest_github(s)
                elif _looks_like_url(s):
                    ctx._ingest_website(s)
                else:
                    p = Path(s).expanduser()
                    if p.is_dir():
                        ctx._ingest(p.resolve())
                    else:
                        skipped.append(f"{s} (not a repo/url/folder)")
                        continue
            except Exception as exc:
                skipped.append(f"{s} ({str(exc)[:80]})")
                continue
            (ok if len(ctx.files) > before else skipped).append(
                s if len(ctx.files) > before else f"{s} (no content)")
        ctx.source_report = {"ok": ok, "skipped": skipped}
        if not ctx.files:
            ctx.cleanup()
            raise RuntimeError("couldn't read any of the sources: "
                               + "; ".join(skipped) or "no sources given")
        return ctx

    # ── source-specific ingesters (merge into self.files) ─────
    def _ingest_github(self, spec: str) -> int:
        """Clone + walk a repo. If the clone fails (private / no access), fall
        back to the public raw README so we still get SOMETHING — and if even
        that fails, return 0 (the caller keeps going with other sources)."""
        url = _normalise_github(spec)
        tmp = tempfile.mkdtemp(prefix="revenant-gh-")
        before = len(self.files)
        try:
            subprocess.run(["git", "clone", "--depth", "1", "--quiet", url, tmp],
                           check=True, capture_output=True, text=True, timeout=90)
            self._ingest(Path(tmp))
            self._extra_tmp.append(tmp)
            return len(self.files) - before
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            shutil.rmtree(tmp, ignore_errors=True)
            readme = _fetch_github_readme(spec)      # public raw fallback
            if readme:
                self.files.setdefault("README.md", readme[:_MAX_FILE_BYTES])
                return len(self.files) - before
            return 0                                  # private/inaccessible — skip

    def _ingest_website(self, url: str, *, max_pages: int = 6) -> int:
        """Fetch the homepage + a few key sub-pages; store their text."""
        import httpx
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        before = len(self.files)
        try:
            with httpx.Client(follow_redirects=True, timeout=15,
                              headers={"User-Agent": _WEB_UA}) as c:
                r = c.get(url)
                if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
                    return 0
                base = str(r.url)
                title = _html_title(r.text)
                self.files["website/home.md"] = (
                    f"# {title}\n(source: {base})\n\n{_html_to_text(r.text)}")[:_MAX_FILE_BYTES]
                for link in _discover_key_pages(r.text, base)[: max_pages - 1]:
                    try:
                        pr = c.get(link)
                        if pr.status_code < 400 and "html" in pr.headers.get("content-type", ""):
                            key = f"website/{_page_slug(link)}.md"
                            self.files.setdefault(
                                key, _html_to_text(pr.text)[:_MAX_FILE_BYTES])
                    except httpx.HTTPError:
                        pass
        except httpx.HTTPError:
            return 0
        return len(self.files) - before

    def cleanup(self) -> None:
        for extra in getattr(self, "_extra_tmp", []):
            shutil.rmtree(extra, ignore_errors=True)
        self._extra_tmp = []
        if self._tmp and os.path.isdir(self._tmp):
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    # ── the walk ──────────────────────────────────────────────
    def _ingest(self, root: Path | None = None) -> None:
        """Walk a directory of text files and MERGE them into self.files.
        Accepts a root so multiple sources (repos/folders) can be combined;
        respects the shared total-bytes budget across calls."""
        root = root or self.root
        candidates: list[tuple[int, Path]] = []
        for p in _walk_text_files(root):
            rel = p.relative_to(root).as_posix()
            score = 0
            if p.name in _PRIORITY_NAMES:
                score -= 100  # bubble to the top
            score += len(rel.split("/"))  # prefer shallow files
            candidates.append((score, p))

        candidates.sort()  # lowest score first (README before deep source)
        total = sum(len(v) for v in self.files.values())  # budget across sources
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
            rel = p.relative_to(root).as_posix()
            if rel in self.files:            # avoid cross-source key collisions
                rel = f"{root.name}/{rel}"
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
        # The company understanding is the foundation everything downstream
        # builds on — run it on the SMART model (gpt-5-mini by default) so the
        # brief is sharp even for a complex/unusual product. Override with
        # BRAIN_MODEL. gpt-5 reasoning models ignore temperature.
        import os as _os
        brain_model = _os.getenv("BRAIN_MODEL", "gpt-5-mini")
        self._summary_cache = complete_strong(
            prompt,
            agent="orchestrator.summary",
            system="You are a sharp principal engineer + GTM strategist "
                   "briefing a colleague. Ground every claim in the sources.",
            offline=offline_stub,
            model=brain_model,
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


# ── multi-source helpers ──────────────────────────────────────────
_WEB_UA = "Mozilla/5.0 (compatible; RevenantContext/1.0)"
_KEY_PAGE_WORDS = ("about", "product", "platform", "solution", "feature",
                   "how-it-works", "how_it_works", "docs", "documentation",
                   "use-case", "use_case", "customer", "technology", "developer",
                   "capabilit", "overview", "why-", "what-we")


def _is_github(s: str) -> bool:
    s = s.lower()
    return ("github.com" in s or s.startswith("git@github")
            or bool(re.fullmatch(r"[\w.-]+/[\w.-]+", s.strip())))


def _looks_like_url(s: str) -> bool:
    s = s.strip().lower()
    return s.startswith(("http://", "https://")) or bool(
        re.match(r"^[\w-]+(\.[\w-]+)+(/|$)", s))  # bare domain like example.com


def _html_title(html_text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    return re.sub(r"\s+", " ", (m.group(1) if m else "")).strip()[:160]


def _html_to_text(html_text: str, *, max_chars: int = 12_000) -> str:
    """Strip a web page to readable text (drop script/style/nav noise)."""
    h = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html_text)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|li|h[1-6]|section|tr)>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    import html as _htmlmod
    h = _htmlmod.unescape(h)
    lines = [ln.strip() for ln in h.splitlines()]
    out, seen = [], set()
    for ln in lines:
        ln = re.sub(r"[ \t]{2,}", " ", ln)
        if len(ln) < 2 or ln in seen:
            continue
        seen.add(ln)
        out.append(ln)
    return "\n".join(out)[:max_chars]


def _discover_key_pages(home_html: str, base_url: str) -> list[str]:
    """Find same-host links to high-signal pages (about/product/docs/…)."""
    from urllib.parse import urljoin, urlparse
    host = urlparse(base_url).netloc
    picked, seen = [], set()
    for m in re.finditer(r'href=["\']([^"\'#?]+)', home_html, re.I):
        href = m.group(1).strip()
        low = href.lower()
        if not any(w in low for w in _KEY_PAGE_WORDS):
            continue
        full = urljoin(base_url, href)
        if urlparse(full).netloc != host:
            continue
        if full.rstrip("/") == base_url.rstrip("/") or full in seen:
            continue
        seen.add(full)
        picked.append(full)
    return picked


def _page_slug(url: str) -> str:
    from urllib.parse import urlparse
    path = urlparse(url).path.strip("/").replace("/", "-")
    return (re.sub(r"[^a-z0-9-]+", "", path.lower())[:40]) or "page"


def _fetch_github_readme(spec: str) -> str:
    """Public raw README for a repo we couldn't clone (private clone, no auth).
    Tries main then master. Returns '' if unavailable."""
    m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$",
                  _normalise_github(spec)) or re.fullmatch(
                      r"([\w.-]+)/([\w.-]+)", spec.strip())
    if not m:
        return ""
    owner, repo = m.group(1), m.group(2)
    import httpx
    for branch in ("main", "master"):
        for fn in ("README.md", "README.rst", "readme.md"):
            try:
                r = httpx.get(
                    f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{fn}",
                    timeout=12, follow_redirects=True,
                    headers={"User-Agent": _WEB_UA})
                if r.status_code == 200 and r.text.strip():
                    return r.text
            except httpx.HTTPError:
                pass
    return ""
