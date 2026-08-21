#!/usr/bin/env python3
"""Fetch the source dependencies declared in repos.json."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


def run_git(
    repo: Path, *args: str, check: bool = True, quiet: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a Git command in a dependency checkout."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
    )


def apply_patch(repo: Path, patch: Path) -> None:
    """Apply a patch once and fail when it matches neither state."""
    if run_git(repo, "apply", "--check", str(patch), check=False, quiet=True).returncode == 0:
        run_git(repo, "apply", str(patch))
        print(f"Applied {patch.relative_to(ROOT)} to {repo.relative_to(ROOT)}")
        return

    if run_git(
        repo, "apply", "--reverse", "--check", str(patch), check=False, quiet=True
    ).returncode == 0:
        print(f"Patch already applied: {patch.relative_to(ROOT)}")
        return

    raise RuntimeError(f"Patch does not match {repo}: {patch}")


def worktree_status(repo: Path) -> str:
    """Return all tracked and untracked dependency changes."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def verify_repo_state(repo: Path, patch: Path | None) -> None:
    """Reject dependency drift beyond the one declared project patch."""
    if patch is None:
        if changes := worktree_status(repo):
            raise RuntimeError(f"Unexpected changes in {repo}:\n{changes}")
        return

    untracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard"],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    ).stdout.strip()
    actual = subprocess.run(
        ["git", "-C", str(repo), "diff", "--binary", "--no-ext-diff", "HEAD", "--"],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    ).stdout.replace("\r\n", "\n")
    expected = patch.read_text(encoding="utf-8").replace("\r\n", "\n")
    if untracked or actual != expected:
        changes = worktree_status(repo)
        raise RuntimeError(f"Unexpected changes in {repo} beyond {patch}:\n{changes}")


def clone_or_update_repo(config: dict[str, Any]) -> None:
    components_root = (ROOT / "components").resolve()
    repo = (ROOT / config["path"]).resolve()
    if not repo.is_relative_to(components_root) or repo == components_root:
        raise ValueError(f"Dependency path must stay under components/: {config['path']}")
    commit = config.get("commit", "")
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise ValueError(f"{config['path']} must declare an immutable 40-character commit SHA")

    if repo.exists():
        if not (repo / ".git").exists():
            raise RuntimeError(f"Existing dependency is not a Git checkout: {repo}")
        run_git(repo, "remote", "set-url", "origin", config["url"])
    else:
        repo.mkdir(parents=True)
        run_git(repo, "init", quiet=True)
        run_git(repo, "remote", "add", "origin", config["url"])

    commit_exists = (
        run_git(repo, "cat-file", "-e", f"{commit}^{{commit}}", check=False, quiet=True).returncode
        == 0
    )
    if not commit_exists:
        run_git(repo, "fetch", "--depth", "1", "origin", commit)

    run_git(repo, "checkout", "--detach", commit)

    if config.get("with_submodules", False):
        run_git(repo, "submodule", "update", "--init", "--recursive")

    patch = (ROOT / config["patch"]).resolve() if config.get("patch") else None
    if patch is not None:
        patches_root = (ROOT / "patches").resolve()
        if not patch.is_relative_to(patches_root) or not patch.is_file():
            raise ValueError(f"Patch path must stay under patches/: {config['patch']}")
    if patch is not None:
        apply_patch(repo, patch)
    verify_repo_state(repo, patch)


def fetch_dependencies() -> None:
    with (ROOT / "repos.json").open(encoding="utf-8") as config_file:
        repositories = json.load(config_file)

    for repository in repositories:
        clone_or_update_repo(repository)


if __name__ == "__main__":
    fetch_dependencies()
