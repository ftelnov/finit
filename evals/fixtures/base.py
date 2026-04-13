"""Base types and helpers for repo fixture generators."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RepoFixture:
    """A generated dummy git repository."""

    path: Path
    language: str
    framework: str
    files: dict[str, str] = field(default_factory=dict)

    def tree(self) -> str:
        """Return a text tree of the repo for inclusion in prompts."""
        lines = []
        for root, dirs, files in os.walk(self.path):
            dirs[:] = [d for d in dirs if d != ".git"]
            level = len(Path(root).relative_to(self.path).parts)
            indent = "  " * level
            lines.append(f"{indent}{Path(root).name}/")
            for f in sorted(files):
                lines.append(f"{indent}  {f}")
        return "\n".join(lines)

    def file_contents_summary(self) -> dict[str, str]:
        """Return {relative_path: content} for all non-git files."""
        result = {}
        for root, dirs, files in os.walk(self.path):
            dirs[:] = [d for d in dirs if d != ".git"]
            for f in sorted(files):
                fp = Path(root) / f
                rel = str(fp.relative_to(self.path))
                result[rel] = fp.read_text()
        return result


def _git_init(path: Path) -> None:
    """Initialize a git repo and make an initial commit."""
    subprocess.run(["git", "init", "-b", "main"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "eval@finit.dev"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Finit Eval"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=path, capture_output=True, check=True)


def _write_files(path: Path, files: dict[str, str]) -> None:
    """Write files dict to disk, creating directories as needed."""
    for rel_path, content in files.items():
        fp = path / rel_path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
