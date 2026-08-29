#!/usr/bin/python3

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


def run(
    args: list[str],
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        env=merged,
    )


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd, check=check)


def init_identity(repo: Path) -> None:
    git(repo, "config", "user.email", "publish-check@example.test")
    git(repo, "config", "user.name", "publish-check")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def commit_tree(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def make_bare_remote(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(source, "clone", "--bare", str(source), str(dest))


class PublishSubmodulesCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.script = (
            Path(__file__).resolve().parents[2] / "scripts" / "publish-submodules.sh"
        )
        self.assertTrue(self.script.is_file(), f"missing shipped script: {self.script}")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _init_repo(self, name: str) -> Path:
        repo = self.root / name
        repo.mkdir()
        git(repo, "init", "-b", "main")
        init_identity(repo)
        return repo

    def _smart_core_commit(self, repo: Path) -> str:
        write(repo / "protocol" / "group" / "smart.go", "package group\n")
        return commit_tree(repo, "smart core")

    def _smart_android_commit(self, repo: Path) -> str:
        write(
            repo / "app" / "build.gradle.kts",
            'android {\n    defaultConfig {\n        applicationId = "io.nekohasekai.sfa.smartbox"\n    }\n}\n',
        )
        return commit_tree(repo, "smart android")

    def _bare_core_commit(self, repo: Path) -> str:
        write(repo / "README", "upstream baseline\n")
        return commit_tree(repo, "bare upstream")

    def _bare_android_commit(self, repo: Path) -> str:
        write(repo / "app" / "build.gradle.kts", 'applicationId = "io.nekohasekai.sfa"\n')
        return commit_tree(repo, "bare android")

    def _superproject(self, core_sha: str, android_sha: str, object_sources: list[Path]) -> Path:
        super_repo = self._init_repo("super")
        for source in object_sources:
            git(super_repo, "fetch", "--depth=1", str(source), "HEAD")
        git(
            super_repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{core_sha},core",
        )
        git(
            super_repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{android_sha},android",
        )
        git(super_repo, "commit", "-m", "gitlinks")
        return super_repo

    def _check(
        self,
        super_repo: Path,
        core_remote: Path,
        android_remote: Path,
        core_sha: str,
        android_sha: str,
    ) -> subprocess.CompletedProcess[str]:
        return run(
            [
                str(self.script),
                "--check",
                "--root",
                str(super_repo),
                "--core-remote",
                str(core_remote),
                "--android-remote",
                str(android_remote),
                "--core-commit",
                core_sha,
                "--android-commit",
                android_sha,
            ],
            check=False,
        )

    def test_rejects_upstream_bare_tree_without_smart_code(self) -> None:
        core_src = self._init_repo("core-bare-src")
        android_src = self._init_repo("android-smart-src")
        core_sha = self._bare_core_commit(core_src)
        android_sha = self._smart_android_commit(android_src)
        core_remote = self.root / "core-bare.git"
        android_remote = self.root / "android-smart.git"
        make_bare_remote(core_src, core_remote)
        make_bare_remote(android_src, android_remote)
        super_repo = self._superproject(
            core_sha, android_sha, [core_remote, android_remote]
        )

        result = self._check(
            super_repo, core_remote, android_remote, core_sha, android_sha
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("lacks protocol/group/smart.go", output)
        self.assertNotIn("CHECK PASS", output)

    def test_rejects_commit_not_reachable_from_fork_remote(self) -> None:
        core_src = self._init_repo("core-smart-src")
        android_src = self._init_repo("android-smart-src")
        published_core = self._smart_core_commit(core_src)
        android_sha = self._smart_android_commit(android_src)
        write(core_src / "protocol" / "group" / "extra.go", "package group\n")
        local_only_core = commit_tree(core_src, "unpushed snapshot")
        core_remote = self.root / "core-published.git"
        android_remote = self.root / "android-smart.git"
        make_bare_remote(core_src, core_remote)
        # Publish only the first commit; the second remains local-only.
        git(core_remote, "update-ref", "refs/heads/main", published_core)
        make_bare_remote(android_src, android_remote)
        super_repo = self._superproject(
            local_only_core,
            android_sha,
            [core_src, android_remote],
        )

        result = self._check(
            super_repo, core_remote, android_remote, local_only_core, android_sha
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("not reachable from fork remote", output)
        self.assertNotIn("CHECK PASS", output)


if __name__ == "__main__":
    unittest.main()
