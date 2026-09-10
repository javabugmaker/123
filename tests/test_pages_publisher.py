from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from institution_scanner import pages_publisher, report_terminal


def _completed(args: list[str], returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout="", stderr="")


def test_ssh_origin_prefers_configured_origin_transport() -> None:
    remote = "git@github.com:javabugmaker/123.git"

    candidates = pages_publisher.publication_remote_candidates(remote)

    assert [(item.label, item.url) for item in candidates] == [
        ("configured origin", remote),
        ("HTTPS", "https://github.com/javabugmaker/123.git"),
    ]
    assert pages_publisher.github_pages_url(remote) == (
        "https://javabugmaker.github.io/123/"
    )


def test_https_transport_forced_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTITUTION_SCANNER_WEB_GIT_TRANSPORT", "https")
    remote = "git@github.com:javabugmaker/123.git"

    candidates = pages_publisher.publication_remote_candidates(remote)

    assert [(item.label, item.url) for item in candidates] == [
        ("HTTPS", "https://github.com/javabugmaker/123.git"),
        ("configured origin", remote),
    ]


def test_clone_falls_back_after_configured_origin_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidates = pages_publisher.publication_remote_candidates(
        "git@github.com:javabugmaker/123.git"
    )
    calls: list[str] = []

    def fake_run_git(
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout: int,
        allow: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout, allow
        calls.append(args[-2])
        if args[-2].startswith("git@github.com:"):
            raise subprocess.TimeoutExpired(args, 90)
        Path(args[-1]).mkdir(parents=True)
        return _completed(args)

    monkeypatch.setattr(pages_publisher, "_run_git", fake_run_git)

    selected = pages_publisher._clone_branch(
        candidates,
        "gh-pages",
        tmp_path / "site",
        timeout=90,
    )

    assert selected.label == "HTTPS"
    assert calls == [
        "git@github.com:javabugmaker/123.git",
        "https://github.com/javabugmaker/123.git",
    ]


def test_clone_retires_leftover_dir_before_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidates = pages_publisher.publication_remote_candidates(
        "git@github.com:javabugmaker/123.git"
    )
    retired: list[Path] = []

    def fake_retire(worktree: Path) -> None:
        retired.append(worktree)
        worktree.mkdir(exist_ok=True)  # simulate a leftover clone dir

    def fake_run_git(
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout: int,
        allow: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout, allow
        if args[-2].startswith("git@github.com:"):
            raise subprocess.TimeoutExpired(args, 90)
        if Path(args[-1]).exists():
            raise RuntimeError(f"destination {args[-1]} already exists")
        Path(args[-1]).mkdir(parents=True)
        return _completed(args)

    monkeypatch.setattr(pages_publisher, "_retire_worktree", fake_retire)
    monkeypatch.setattr(pages_publisher, "_run_git", fake_run_git)

    selected = pages_publisher._clone_branch(
        candidates,
        "gh-pages",
        tmp_path / "site",
        timeout=90,
    )

    assert selected.label == "HTTPS"
    assert len(retired) == 2


def test_retire_worktree_quarantines_locked_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stale = tmp_path / "site"
    stale.mkdir()
    (stale / "file.txt").write_text("x", encoding="utf-8")

    def locked_rmtree(_path: object) -> None:
        raise OSError("locked on Windows")

    monkeypatch.setattr(pages_publisher.shutil, "rmtree", locked_rmtree)

    pages_publisher._retire_worktree(stale)

    assert not stale.exists()
    leftovers = list(tmp_path.iterdir())
    assert len(leftovers) == 1
    assert leftovers[0].name.startswith("site.stale-")


def test_push_falls_back_to_https(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidates = pages_publisher.publication_remote_candidates(
        "git@github.com:javabugmaker/123.git"
    )
    push_urls: list[str] = []
    active_url = ""

    def fake_run_git(
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout: int,
        allow: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        nonlocal active_url
        del cwd, timeout, allow
        if args[:4] == ["remote", "set-url", "--push", "origin"]:
            active_url = args[4]
            return _completed(args)
        if args[0] == "push":
            push_urls.append(active_url)
            if active_url.startswith("git@github.com:"):
                raise RuntimeError("ssh transport down")
        return _completed(args)

    monkeypatch.setattr(pages_publisher, "_run_git", fake_run_git)

    selected = pages_publisher._push_branch(
        tmp_path,
        candidates,
        "gh-pages",
        timeout=90,
    )

    assert selected.label == "HTTPS"
    assert push_urls == [
        "git@github.com:javabugmaker/123.git",
        "https://github.com/javabugmaker/123.git",
    ]


def test_report_terminal_routes_to_canonical_publisher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    site_dir = tmp_path / "site"
    (site_dir / "reports").mkdir(parents=True)
    (site_dir / "index.html").write_text("index", encoding="utf-8")

    def fake_publish_site_files(
        site_dir_arg: Path,
        *,
        repo_root: Path,
        branch: str,
        report_date: str,
        archive_renderer: object,
    ) -> pages_publisher.PagesPublication:
        assert site_dir_arg == site_dir
        assert repo_root == tmp_path
        assert branch == "gh-pages"
        assert report_date == "2026-08-28"
        assert callable(archive_renderer)
        return pages_publisher.PagesPublication(
            report_date=report_date,
            page_url="https://javabugmaker.github.io/123/",
            message="published via HTTPS",
        )

    monkeypatch.setattr(
        pages_publisher,
        "publish_site_files",
        fake_publish_site_files,
    )

    result = report_terminal.publish_site(
        site_dir,
        repo_root=tmp_path,
        report_date="2026-08-28",
    )

    assert result.published is True
    assert result.publish_message == "published via HTTPS"
    assert result.page_url == "https://javabugmaker.github.io/123/"
