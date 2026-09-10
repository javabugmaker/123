"""Resilient GitHub Pages transport for the canonical report terminal.

The report is public, so cloning the current ``gh-pages`` snapshot does not
need the configured push transport.  In particular, a repository cloned over
SSH should not make page publication depend on port 22 merely to read a public
branch.  This module therefore prefers HTTPS for reads and tries both HTTPS and
the configured origin for the small authenticated push.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

WEB_GIT_TIMEOUT_ENV = "INSTITUTION_SCANNER_WEB_GIT_TIMEOUT"
DEFAULT_GIT_TIMEOUT_SECONDS = 90
MIN_GIT_TIMEOUT_SECONDS = 15
MAX_GIT_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class GitRemoteCandidate:
    """One credential/transport candidate without embedding secrets in labels."""

    label: str
    url: str


@dataclass(frozen=True)
class PagesPublication:
    """Transport result converted to the legacy WebReportResult by the facade."""

    report_date: str
    page_url: str
    message: str


def _github_repository(remote: str) -> tuple[str, str] | None:
    value = str(remote or "").strip()
    patterns = (
        r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
    return None


def github_https_remote(remote: str) -> str:
    """Return a credential-free public HTTPS URL for a GitHub origin."""
    repository = _github_repository(remote)
    if repository is None:
        return ""
    owner, name = repository
    return f"https://github.com/{owner}/{name}.git"


def github_pages_url(remote: str) -> str:
    repository = _github_repository(remote)
    if repository is None:
        return ""
    owner, name = repository
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner}.github.io/"
    return f"https://{owner}.github.io/{name}/"


def _has_configured_origin(remote: str, https_remote: str) -> bool:
    return bool(remote.strip()) and remote.rstrip("/") != https_remote.rstrip("/")


def publication_remote_candidates(remote: str) -> tuple[GitRemoteCandidate, ...]:
    """Return transport candidates, preferring the configured origin (SSH).

    The configured origin is usually ``git@github.com:...`` (SSH), which keeps
    working on machines where HTTPS to github.com is firewalled --- exactly the
    state that makes an HTTPS-first order time out the clone and only stumble
    onto a working transport after a long delay (or fail on a leftover dir).
    The credential-free public HTTPS URL is retained as a fallback.  Set the
    environment variable ``INSTITUTION_SCANNER_WEB_GIT_TRANSPORT=https`` to
    force the legacy HTTPS-first order.
    """
    https_remote = github_https_remote(remote)
    if not https_remote:
        return ()
    https_candidate = GitRemoteCandidate("HTTPS", https_remote)
    configured = GitRemoteCandidate("configured origin", remote)
    prefer = os.environ.get(
        "INSTITUTION_SCANNER_WEB_GIT_TRANSPORT", ""
    ).strip().lower()
    if prefer in {"http", "https"}:
        candidates = [https_candidate]
        if _has_configured_origin(remote, https_remote):
            candidates.append(configured)
    else:
        candidates = (
            [configured] if _has_configured_origin(remote, https_remote) else []
        )
        candidates.append(https_candidate)
    return tuple(candidates)


def _git_timeout_seconds() -> int:
    raw = os.environ.get(WEB_GIT_TIMEOUT_ENV, "").strip()
    try:
        configured = int(raw) if raw else DEFAULT_GIT_TIMEOUT_SECONDS
    except ValueError:
        configured = DEFAULT_GIT_TIMEOUT_SECONDS
    return max(MIN_GIT_TIMEOUT_SECONDS, min(MAX_GIT_TIMEOUT_SECONDS, configured))


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int,
    allow: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode not in allow:
        detail = (completed.stderr or completed.stdout or "git command failed").strip()
        raise RuntimeError(f"WEB_REPORT_GIT_FAILED: {' '.join(args)}: {detail}")
    return completed


def _failure_text(exc: BaseException) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timed out"
    text = str(exc).strip().replace("\n", " ")
    return text[-500:] if text else type(exc).__name__


def _branch_exists(
    candidates: Sequence[GitRemoteCandidate],
    branch: str,
    *,
    timeout: int,
) -> bool:
    errors: list[str] = []
    for candidate in candidates:
        try:
            result = _run_git(
                [
                    "ls-remote",
                    "--exit-code",
                    "--heads",
                    candidate.url,
                    branch,
                ],
                timeout=timeout,
                allow=(0, 2),
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(f"{candidate.label}: {_failure_text(exc)}")
            continue
        return result.returncode == 0
    raise RuntimeError("WEB_REPORT_REMOTE_UNREACHABLE: " + "; ".join(errors))


def _retire_worktree(worktree: Path) -> None:
    """Remove a stale clone dir, or quarantine it if files are locked.

    A clone killed by timeout can leave locked files that ``shutil.rmtree``
    cannot delete on Windows, which then makes the next transport fail with
    ``destination path already exists``.  Move the leftover aside so the next
    candidate can clone into a fresh empty directory.
    """
    if not worktree.exists():
        return
    try:
        shutil.rmtree(worktree)
        return
    except OSError:
        pass
    try:
        worktree.rename(Path(str(worktree) + f".stale-{os.getpid()}"))
    except OSError:
        pass


def _clone_branch(
    candidates: Sequence[GitRemoteCandidate],
    branch: str,
    worktree: Path,
    *,
    timeout: int,
) -> GitRemoteCandidate:
    errors: list[str] = []
    for candidate in candidates:
        _retire_worktree(worktree)
        try:
            _run_git(
                [
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--branch",
                    branch,
                    "--single-branch",
                    candidate.url,
                    str(worktree),
                ],
                timeout=timeout,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(f"{candidate.label}: {_failure_text(exc)}")
            continue
        return candidate
    raise RuntimeError("WEB_REPORT_CLONE_FAILED: " + "; ".join(errors))


def _push_branch(
    worktree: Path,
    candidates: Sequence[GitRemoteCandidate],
    branch: str,
    *,
    timeout: int,
) -> GitRemoteCandidate:
    errors: list[str] = []
    for candidate in candidates:
        try:
            _run_git(
                ["remote", "set-url", "--push", "origin", candidate.url],
                cwd=worktree,
                timeout=timeout,
            )
            _run_git(
                ["push", "origin", f"HEAD:{branch}"],
                cwd=worktree,
                timeout=timeout,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(f"{candidate.label}: {_failure_text(exc)}")
            continue
        return candidate
    raise RuntimeError("WEB_REPORT_PUSH_FAILED: " + "; ".join(errors))


def publish_site_files(
    site_dir: Path,
    *,
    repo_root: Path,
    branch: str,
    report_date: str,
    archive_renderer: Callable[[Path], str],
) -> PagesPublication:
    """Commit the generated static site to ``branch`` with transport fallback."""
    site_dir = Path(site_dir)
    repo_root = Path(repo_root)
    if not (site_dir / "index.html").is_file():
        raise RuntimeError("WEB_REPORT_SITE_MISSING: index.html not found")

    timeout = _git_timeout_seconds()
    remote = _run_git(
        ["-C", str(repo_root), "remote", "get-url", "origin"],
        timeout=timeout,
    ).stdout.strip()
    candidates = publication_remote_candidates(remote)
    page_url = github_pages_url(remote)
    if not candidates or not page_url:
        raise RuntimeError("WEB_REPORT_UNSUPPORTED_REMOTE: origin is not github.com")

    exists = _branch_exists(candidates, branch, timeout=timeout)
    with tempfile.TemporaryDirectory(prefix="institution-web-") as temp_dir:
        worktree = Path(temp_dir) / "site"
        if exists:
            _clone_branch(candidates, branch, worktree, timeout=timeout)
        else:
            worktree.mkdir(parents=True, exist_ok=True)
            _run_git(["init", "--quiet"], cwd=worktree, timeout=timeout)
            _run_git(
                ["remote", "add", "origin", candidates[0].url],
                cwd=worktree,
                timeout=timeout,
            )
            _run_git(
                ["checkout", "--orphan", branch],
                cwd=worktree,
                timeout=timeout,
            )

        shutil.copy2(site_dir / "index.html", worktree / "index.html")
        shutil.copy2(site_dir / ".nojekyll", worktree / ".nojekyll")
        shutil.copytree(
            site_dir / "reports",
            worktree / "reports",
            dirs_exist_ok=True,
        )
        publish_paths = ["index.html", ".nojekyll", "reports"]
        assets_dir = site_dir / "assets"
        if assets_dir.is_dir():
            shutil.copytree(
                assets_dir,
                worktree / "assets",
                dirs_exist_ok=True,
            )
            publish_paths.append("assets")
        performance_page = site_dir / "performance.html"
        if performance_page.is_file():
            shutil.copy2(performance_page, worktree / "performance.html")
            publish_paths.append("performance.html")
        backtest_page = site_dir / "backtest.html"
        if backtest_page.is_file():
            shutil.copy2(backtest_page, worktree / "backtest.html")
            publish_paths.append("backtest.html")
        (worktree / "reports" / "index.html").write_text(
            archive_renderer(worktree),
            encoding="utf-8",
        )

        _run_git(["add", "--", *publish_paths], cwd=worktree, timeout=timeout)
        diff = _run_git(
            ["diff", "--cached", "--quiet"],
            cwd=worktree,
            timeout=timeout,
            allow=(0, 1),
        )
        if diff.returncode == 1:
            stamp = report_date or date.today().isoformat()
            _run_git(
                [
                    "-c",
                    "user.name=InstitutionScanner",
                    "-c",
                    "user.email=institution-scanner@users.noreply.github.com",
                    "commit",
                    "--quiet",
                    "-m",
                    f"report: research briefing {stamp}",
                ],
                cwd=worktree,
                timeout=timeout,
            )
            transport = _push_branch(
                worktree,
                candidates,
                branch,
                timeout=timeout,
            )
            message = f"published {stamp} to {branch} via {transport.label}"
        else:
            message = "website already up to date"

    return PagesPublication(
        report_date=report_date or date.today().isoformat(),
        page_url=page_url,
        message=message,
    )
