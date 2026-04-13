"""Eval repo fixture generators — one module per language.

Usage:
    from fixtures import REPO_GENERATORS, RepoFixture
    repo = REPO_GENERATORS["python-flask"](tmp_path)
"""

from fixtures.base import RepoFixture, _git_init, _write_files
from fixtures.python import (
    python_django_project,
    python_fastapi_project,
    python_flask_no_requirements,
    python_flask_project,
    python_flask_with_auth_middleware,
    python_flask_with_manifest,
    python_monorepo,
)
from fixtures.go import (
    go_chi_project,
    go_missing_deps,
    go_stdlib_project,
)
from fixtures.node import (
    node_express_project,
    node_tap_project,
    node_typescript_project,
)
from fixtures.rust import (
    rust_actix_project,
)

REPO_GENERATORS = {
    "python-flask": python_flask_project,
    "python-fastapi": python_fastapi_project,
    "python-flask-pyproject": python_flask_no_requirements,
    "python-django": python_django_project,
    "python-flask-manifest": python_flask_with_manifest,
    "python-flask-auth": python_flask_with_auth_middleware,
    "python-monorepo": python_monorepo,
    "go-chi": go_chi_project,
    "go-stdlib": go_stdlib_project,
    "go-missing-deps": go_missing_deps,
    "node-express": node_express_project,
    "node-typescript": node_typescript_project,
    "node-tap": node_tap_project,
    "rust-actix": rust_actix_project,
}

__all__ = ["REPO_GENERATORS", "RepoFixture"]
