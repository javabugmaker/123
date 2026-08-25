from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docker_uses_same_python_major_minor_as_ci() -> None:
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/static-quality.yml").read_text(
        encoding="utf-8"
    )
    assert "FROM python:3.11-slim-bookworm" in docker
    assert "python-version: '3.11'" in workflow


def test_docker_and_ci_both_use_reviewed_constraints() -> None:
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/static-quality.yml").read_text(
        encoding="utf-8"
    )
    assert "constraints-ci.txt" in docker
    assert "-c constraints-ci.txt -r requirements.txt" in docker
    assert "-c constraints-ci.txt -r requirements.txt" in workflow
