FROM rust:1.88-bookworm AS builder
RUN apt-get update && apt-get install -y cmake protobuf-compiler && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY orchestrator/ orchestrator/
COPY prompts/ prompts/
COPY llm-router/Cargo.toml llm-router/Cargo.toml
RUN mkdir -p llm-router/src && echo "fn main() {}" > llm-router/src/main.rs
RUN cargo build --release --package finit-orchestrator
RUN cp target/release/finit-orchestrator /app/finit-orchestrator

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y ca-certificates wget && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/finit-orchestrator /usr/local/bin/finit-orchestrator
EXPOSE 8080
CMD ["finit-orchestrator"]
