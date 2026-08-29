from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from institution_scanner import pages_publisher, report_terminal


def _completed(args: list[str], returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout="", stderr="")


def test_ssh_origin_prefers_public_https_transport() -> None:
    remote = "git@github.com:javabugmaker/123.git"

    candidates = pages_publisher.publication_remote_candidates(remote)

    assert [(item.label, item.url) for item in candidates] == [
        ("HTTPS", "https://github.com/javabugmaker/123.git"),
        ("configured origin", remote),
    ]
    assert pages_publisher.github_pages_url(remote) == (
        "https://javabugmaker.github.io/123/"
    )


def test_clone_falls_back_after_https_timeout(
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
        if args[-2].startswith("https://"):
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

    assert selected.label == "configured origin"
    assert calls == [
        "https://github.com/javabugmaker/123.git",
        "git@github.com:javabugmaker/123.git",
    ]


def test_push_falls_back_to_configured_origin(
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
            if active_url.startswith("https://"):
                raise RuntimeError("credentials unavailable")
        return _completed(args)

    monkeypatch.setattr(pages_publisher, "_run_git", fake_run_git)

    selected = pages_publisher._push_branch(
        tmp_path,
        candidates,
        "gh-pages",
        timeout=90,
    )

    assert selected.label == "configured origin"
    assert push_urls == [
        "https://github.com/javabugmaker/123.git",
        "git@github.com:javabugmaker/123.git",
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
