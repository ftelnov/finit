from __future__ import annotations

import tempfile
from pathlib import Path

from fixtures.base import RepoFixture, _git_init, _write_files


def go_chi_project(tmp_path: Path | None = None) -> RepoFixture:
    """Minimal Go project with chi router."""
    path = tmp_path or Path(tempfile.mkdtemp(prefix="eval-go-chi-"))
    files = {
        "go.mod": (
            "module github.com/example/demo-service\n\n"
            "go 1.22.0\n\n"
            "require github.com/go-chi/chi/v5 v5.0.12\n"
        ),
        "main.go": (
            "package main\n\n"
            "import (\n"
            '\t"encoding/json"\n'
            '\t"log"\n'
            '\t"net/http"\n\n'
            '\t"github.com/go-chi/chi/v5"\n'
            '\t"github.com/go-chi/chi/v5/middleware"\n'
            ")\n\n"
            "func main() {\n"
            "\tr := chi.NewRouter()\n"
            "\tr.Use(middleware.Logger)\n"
            "\tr.Get(\"/\", func(w http.ResponseWriter, r *http.Request) {\n"
            "\t\tjson.NewEncoder(w).Encode(map[string]string{\"status\": \"ok\"})\n"
            "\t})\n"
            '\tlog.Fatal(http.ListenAndServe(":8080", r))\n'
            "}\n"
        ),
        "main_test.go": (
            "package main\n\n"
            "import (\n"
            '\t"net/http"\n'
            '\t"net/http/httptest"\n'
            '\t"testing"\n'
            ")\n\n"
            "func TestRoot(t *testing.T) {\n"
            "\treq := httptest.NewRequest(http.MethodGet, \"/\", nil)\n"
            "\tw := httptest.NewRecorder()\n"
            "\t// placeholder test\n"
            "\tif w.Code != http.StatusOK {\n"
            "\t\t// initial state is 200\n"
            "\t}\n"
            "\t_ = req\n"
            "}\n"
        ),
        ".gitignore": "bin/\n*.exe\n",
    }
    _write_files(path, files)
    _git_init(path)
    return RepoFixture(path=path, language="go", framework="chi", files=files)


def go_stdlib_project(tmp_path: Path | None = None) -> RepoFixture:
    """Go project using only standard library."""
    path = tmp_path or Path(tempfile.mkdtemp(prefix="eval-go-stdlib-"))
    files = {
        "go.mod": "module github.com/example/simple-api\n\ngo 1.22.0\n",
        "cmd/server/main.go": (
            "package main\n\n"
            "import (\n"
            '\t"encoding/json"\n'
            '\t"log"\n'
            '\t"net/http"\n'
            ")\n\n"
            "func main() {\n"
            "\tmux := http.NewServeMux()\n"
            '\tmux.HandleFunc("GET /", handleRoot)\n'
            '\tlog.Fatal(http.ListenAndServe(":8080", mux))\n'
            "}\n\n"
            "func handleRoot(w http.ResponseWriter, r *http.Request) {\n"
            '\tw.Header().Set("Content-Type", "application/json")\n'
            "\tjson.NewEncoder(w).Encode(map[string]string{\"status\": \"ok\"})\n"
            "}\n"
        ),
        "cmd/server/main_test.go": (
            "package main\n\n"
            "import (\n"
            '\t"net/http"\n'
            '\t"net/http/httptest"\n'
            '\t"testing"\n'
            ")\n\n"
            "func TestHandleRoot(t *testing.T) {\n"
            "\treq := httptest.NewRequest(http.MethodGet, \"/\", nil)\n"
            "\tw := httptest.NewRecorder()\n"
            "\thandleRoot(w, req)\n"
            "\tif w.Code != http.StatusOK {\n"
            '\t\tt.Errorf("expected 200, got %d", w.Code)\n'
            "\t}\n"
            "}\n"
        ),
        ".gitignore": "bin/\n",
    }
    _write_files(path, files)
    _git_init(path)
    return RepoFixture(path=path, language="go", framework="stdlib", files=files)


def go_missing_deps(tmp_path: Path | None = None) -> RepoFixture:
    """Go project that references a dependency not in go.mod — forces bootstrapper
    to detect that `go mod tidy` is needed."""
    path = tmp_path or Path(tempfile.mkdtemp(prefix="eval-go-missing-dep-"))
    files = {
        "go.mod": "module github.com/example/svc\n\ngo 1.22.0\n",
        "main.go": (
            "package main\n\n"
            "import (\n"
            '\t"log"\n'
            '\t"net/http"\n\n'
            '\t"github.com/go-chi/chi/v5"\n'
            ")\n\n"
            "func main() {\n"
            "\tr := chi.NewRouter()\n"
            "\tr.Get(\"/\", func(w http.ResponseWriter, r *http.Request) {\n"
            '\t\tw.Write([]byte("ok"))\n'
            "\t})\n"
            '\tlog.Fatal(http.ListenAndServe(":8080", r))\n'
            "}\n"
        ),
        ".gitignore": "bin/\n",
    }
    _write_files(path, files)
    _git_init(path)
    return RepoFixture(path=path, language="go", framework="chi", files=files)
