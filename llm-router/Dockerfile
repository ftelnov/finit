FROM rust:1.88-bookworm AS builder
RUN apt-get update && apt-get install -y cmake protobuf-compiler && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# Copy workspace manifest
COPY Cargo.toml Cargo.lock ./

# Copy llm-router source
COPY llm-router/ llm-router/

# Create dummy orchestrator src for workspace compilation
COPY orchestrator/Cargo.toml orchestrator/Cargo.toml
RUN mkdir -p orchestrator/src && echo "fn main() {}" > orchestrator/src/main.rs

# Build the llm-router
RUN cargo build --release --package finit-llm-router
RUN cp target/release/finit-llm-router /app/finit-llm-router

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y ca-certificates wget && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/finit-llm-router /usr/local/bin/finit-llm-router
EXPOSE 8081
CMD ["finit-llm-router"]
