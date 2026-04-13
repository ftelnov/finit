from __future__ import annotations

import tempfile
from pathlib import Path

from fixtures.base import RepoFixture, _git_init, _write_files


def rust_actix_project(tmp_path: Path | None = None) -> RepoFixture:
    """Minimal Rust Actix-web project."""
    path = tmp_path or Path(tempfile.mkdtemp(prefix="eval-rust-actix-"))
    files = {
        "Cargo.toml": (
            "[package]\n"
            'name = "demo-api"\n'
            'version = "0.1.0"\n'
            'edition = "2021"\n\n'
            "[dependencies]\n"
            'actix-web = "4"\n'
            'serde = { version = "1", features = ["derive"] }\n'
            'serde_json = "1"\n'
            'tokio = { version = "1", features = ["full"] }\n'
        ),
        "src/main.rs": (
            "use actix_web::{get, web, App, HttpResponse, HttpServer, Responder};\n"
            "use serde::Serialize;\n\n"
            "#[derive(Serialize)]\n"
            "struct Status {\n"
            "    status: String,\n"
            "}\n\n"
            "#[get(\"/\")]\n"
            "async fn index() -> impl Responder {\n"
            '    HttpResponse::Ok().json(Status { status: "ok".to_string() })\n'
            "}\n\n"
            "#[actix_web::main]\n"
            "async fn main() -> std::io::Result<()> {\n"
            "    HttpServer::new(|| App::new().service(index))\n"
            '        .bind("0.0.0.0:8080")?\n'
            "        .run()\n"
            "        .await\n"
            "}\n"
        ),
        ".gitignore": "target/\n",
    }
    _write_files(path, files)
    _git_init(path)
    return RepoFixture(path=path, language="rust", framework="actix-web", files=files)
