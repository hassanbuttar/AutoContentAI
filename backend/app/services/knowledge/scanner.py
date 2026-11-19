"""Read-only project scanning: local folders, local git, GitHub and GitLab.

Security: project content is untrusted. We only read files and run read-only git commands with
hooks, fsmonitor, external diff and textconv disabled. Project code is never executed.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next", ".nuxt", "out",
             "target", "bin", "obj", ".idea", ".vscode", "coverage", ".pytest_cache", ".mypy_cache", "vendor", "Pods",
             ".gradle", ".dart_tool", ".turbo", ".cache", "storage", "tmp", ".terraform"}
DOC_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc"}
DOC_NAMES = re.compile(r"(?i)^(readme|changelog|changes|history|release[-_ ]?notes|features|roadmap|about)(\.[a-z]+)?$")
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".swift", ".cs", ".rb", ".php", ".dart",
            ".vue", ".svelte", ".c", ".cpp", ".h", ".scala", ".ex", ".sql"}
FEATURE_DIR_HINTS = {"features", "modules", "pages", "routes", "screens", "views", "app", "api", "services", "domains", "components"}
MAX_FILES = 25_000
MAX_DOC_CHARS = 30_000
MAX_HASH_BYTES = 2_000_000


@dataclass
class ScanResult:
    files: dict[str, str] = field(default_factory=dict)  # rel path -> content hash
    docs: dict[str, str] = field(default_factory=dict)  # rel path -> text (bounded)
    readme: str = ""
    manifest_info: dict = field(default_factory=dict)  # package.json / pyproject summary
    tree: list[str] = field(default_factory=list)  # top-level directory summary lines
    feature_modules: list[str] = field(default_factory=list)
    languages: dict[str, int] = field(default_factory=dict)
    truncated: bool = False

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for k in sorted(self.files):
            h.update(k.encode())
            h.update(self.files[k].encode())
        return h.hexdigest()[:40]


def _matches(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) for p in patterns)


def _file_hash(p: Path) -> str:
    try:
        st = p.stat()
        if st.st_size > MAX_HASH_BYTES:
            return f"size:{st.st_size}:{int(st.st_mtime)}"
        return hashlib.sha1(p.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def _read_text(p: Path, limit: int = MAX_DOC_CHARS) -> str:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def scan_local(root: Path, include: list[str] | None = None, exclude: list[str] | None = None,
               docs_only: bool = False) -> ScanResult:
    root = Path(root).resolve()
    res = ScanResult()
    include = include or []
    exclude = exclude or []
    top_counts: dict[str, int] = {}
    modules: set[str] = set()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        rel_dir = Path(dirpath).relative_to(root)
        parts = rel_dir.parts
        # feature module heuristics: children of hint dirs, e.g. src/features/billing
        if len(parts) >= 1 and parts[-1].lower() in FEATURE_DIR_HINTS:
            for d in dirnames[:40]:
                if not d.startswith(("_", "[")) or d.startswith("["):
                    modules.add(f"{'/'.join(parts)}/{d}")
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.is_symlink():
                continue  # never follow symlinks out of the allowed root
            rel = (rel_dir / fn).as_posix()
            if include and not _matches(rel, include):
                continue
            if exclude and _matches(rel, exclude):
                continue
            ext = p.suffix.lower()
            is_doc = ext in DOC_EXT or bool(DOC_NAMES.match(fn))
            if docs_only and not is_doc:
                continue
            count += 1
            if count > MAX_FILES:
                res.truncated = True
                break
            res.files[rel] = _file_hash(p)
            if ext in CODE_EXT:
                res.languages[ext] = res.languages.get(ext, 0) + 1
            top = parts[0] if parts else "."
            top_counts[top] = top_counts.get(top, 0) + 1
            if is_doc and len(res.docs) < 60:
                res.docs[rel] = _read_text(p)
                if not res.readme and fn.lower().startswith("readme") and len(parts) == 0:
                    res.readme = res.docs[rel]
            if fn in ("package.json", "pyproject.toml", "Cargo.toml", "pubspec.yaml", "composer.json") and len(parts) <= 1:
                res.manifest_info[rel] = _manifest_summary(p)
        if res.truncated:
            break
    res.tree = [f"{k}/ ({v} files)" if k != "." else f"(root) ({v} files)" for k, v in sorted(top_counts.items(), key=lambda x: -x[1])[:25]]
    res.feature_modules = sorted(modules)[:80]
    return res


def _manifest_summary(p: Path) -> dict:
    text = _read_text(p, 50_000)
    out: dict = {}
    if p.name in ("package.json", "composer.json"):
        try:
            data = json.loads(text)
            out = {k: data.get(k) for k in ("name", "description", "version", "keywords") if data.get(k)}
            deps = list((data.get("dependencies") or {}).keys())[:30]
            if deps:
                out["dependencies"] = deps
        except json.JSONDecodeError:
            pass
    else:
        for key in ("name", "description", "version"):
            m = re.search(rf'(?m)^\s*{key}\s*[=:]\s*["\']?([^"\'\n]+)', text)
            if m:
                out[key] = m.group(1).strip()[:300]
    return out


# ------------------------------------------------------------------ git (local, read-only)
_EMPTY_HOOKS = Path(tempfile.gettempdir()) / "autocontent-empty-hooks"


def _git(repo: Path, *args: str, timeout: int = 60) -> str:
    _EMPTY_HOOKS.mkdir(exist_ok=True)
    cmd = ["git", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={_EMPTY_HOOKS.as_posix()}",
           "-c", f"safe.directory={Path(repo).resolve().as_posix()}", "-c", "diff.external=",
           "-c", "core.pager=cat", "-C", str(repo), *args]
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    out = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, shell=False)
    if out.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {out.stderr.decode('utf-8', 'replace')[:300]}")
    return out.stdout.decode("utf-8", "replace")


def is_git_repo(path: Path) -> bool:
    try:
        return _git(path, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except Exception:
        return False


def git_head(repo: Path, branch: str | None = None) -> str:
    return _git(repo, "rev-parse", branch or "HEAD").strip()


SEP = "\x1e"


def git_commits(repo: Path, since_sha: str | None, branch: str | None = None, max_count: int = 200) -> list[dict]:
    ref = branch or "HEAD"
    rng = f"{since_sha}..{ref}" if since_sha else ref
    fmt = f"{SEP}%H%x1f%aI%x1f%an%x1f%s%x1f%b%x1f"
    try:
        raw = _git(repo, "log", rng, f"--max-count={max_count}", "--no-merges", f"--format={fmt}",
                   "--numstat", "--no-ext-diff", "--no-textconv")
    except RuntimeError:
        if since_sha:  # history rewritten / unknown sha: fall back to recent commits
            raw = _git(repo, "log", ref, "--max-count=30", "--no-merges", f"--format={fmt}", "--numstat",
                       "--no-ext-diff", "--no-textconv")
        else:
            raise
    commits = []
    for chunk in raw.split(SEP)[1:]:
        head, _, stat = chunk.partition("\x1f\n") if "\x1f\n" in chunk else (chunk, "", "")
        fields = head.split("\x1f")
        if len(fields) < 5:
            continue
        files = []
        for line in stat.strip().splitlines():
            cols = line.split("\t")
            if len(cols) == 3:
                a, d, f = cols
                files.append({"path": f, "added": int(a) if a.isdigit() else 0, "deleted": int(d) if d.isdigit() else 0})
        commits.append({"sha": fields[0], "date": fields[1], "author": fields[2], "subject": fields[3][:300],
                        "body": fields[4].strip()[:800], "files": files[:60]})
    return commits


def git_tags(repo: Path, limit: int = 10) -> list[dict]:
    try:
        raw = _git(repo, "tag", "--sort=-creatordate", f"--format=%(refname:short)%09%(creatordate:iso-strict)%09%(subject)")
    except RuntimeError:
        return []
    out = []
    for line in raw.splitlines()[:limit]:
        parts = line.split("\t")
        if parts and parts[0]:
            out.append({"tag": parts[0], "date": parts[1] if len(parts) > 1 else "", "subject": parts[2] if len(parts) > 2 else ""})
    return out


# ------------------------------------------------------------------ hosted repos (API, no clone)
@dataclass
class RemoteRepoData:
    head: str | None
    description: str = ""
    readme: str = ""
    commits: list[dict] = field(default_factory=list)
    releases: list[dict] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)


def fetch_github(repo: str, branch: str | None, since_sha: str | None, token: str | None,
                 client: httpx.Client | None = None) -> RemoteRepoData:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("GitHub repository must look like owner/name")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "autocontent-ai"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    c = client or httpx.Client(timeout=30, headers=headers)
    base = f"https://api.github.com/repos/{repo}"
    info = c.get(base, headers=headers)
    info.raise_for_status()
    meta = info.json()
    br = branch or meta.get("default_branch") or "main"
    readme = ""
    r = c.get(f"{base}/readme", headers={**headers, "Accept": "application/vnd.github.raw"})
    if r.status_code == 200:
        readme = r.text[:MAX_DOC_CHARS]
    commits: list[dict] = []
    page = 1
    while page <= 3:
        r = c.get(f"{base}/commits", params={"sha": br, "per_page": 100, "page": page}, headers=headers)
        r.raise_for_status()
        batch = r.json()
        stop = False
        for cm in batch:
            if since_sha and cm["sha"] == since_sha:
                stop = True
                break
            msg = cm["commit"]["message"]
            subject, _, body = msg.partition("\n")
            commits.append({"sha": cm["sha"], "date": cm["commit"]["author"]["date"], "author": cm["commit"]["author"]["name"],
                            "subject": subject[:300], "body": body.strip()[:800], "files": []})
        if stop or len(batch) < 100 or not since_sha:
            break
        page += 1
    rel = c.get(f"{base}/releases", params={"per_page": 10}, headers=headers)
    releases = []
    if rel.status_code == 200:
        for x in rel.json():
            if x.get("draft"):
                continue
            releases.append({"tag": x.get("tag_name"), "name": x.get("name") or x.get("tag_name"), "date": x.get("published_at"),
                             "body": (x.get("body") or "")[:3000], "prerelease": x.get("prerelease", False)})
    head = commits[0]["sha"] if commits else since_sha
    return RemoteRepoData(head=head, description=meta.get("description") or "", readme=readme, commits=commits[:200],
                          releases=releases, topics=meta.get("topics") or [])


def fetch_gitlab(project: str, branch: str | None, since_sha: str | None, token: str | None,
                 base_url: str = "https://gitlab.com", client: httpx.Client | None = None) -> RemoteRepoData:
    headers = {"User-Agent": "autocontent-ai"}
    if token:
        headers["PRIVATE-TOKEN"] = token
    c = client or httpx.Client(timeout=30)
    pid = quote(project, safe="")
    base = f"{base_url.rstrip('/')}/api/v4/projects/{pid}"
    info = c.get(base, headers=headers)
    info.raise_for_status()
    meta = info.json()
    br = branch or meta.get("default_branch") or "main"
    readme = ""
    for name in ("README.md", "README.rst", "README"):
        r = c.get(f"{base}/repository/files/{quote(name, safe='')}/raw", params={"ref": br}, headers=headers)
        if r.status_code == 200:
            readme = r.text[:MAX_DOC_CHARS]
            break
    r = c.get(f"{base}/repository/commits", params={"ref_name": br, "per_page": 100}, headers=headers)
    r.raise_for_status()
    commits = []
    for cm in r.json():
        if since_sha and cm["id"] == since_sha:
            break
        commits.append({"sha": cm["id"], "date": cm.get("authored_date"), "author": cm.get("author_name"),
                        "subject": (cm.get("title") or "")[:300], "body": (cm.get("message") or "").partition("\n")[2].strip()[:800],
                        "files": []})
    releases = []
    rr = c.get(f"{base}/releases", params={"per_page": 10}, headers=headers)
    if rr.status_code == 200:
        for x in rr.json():
            releases.append({"tag": x.get("tag_name"), "name": x.get("name"), "date": x.get("released_at"),
                             "body": (x.get("description") or "")[:3000], "prerelease": x.get("upcoming_release", False)})
    head = commits[0]["sha"] if commits else since_sha
    return RemoteRepoData(head=head, description=meta.get("description") or "", readme=readme, commits=commits,
                          releases=releases, topics=meta.get("topics") or [])
