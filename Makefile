.PHONY: up down test test-integration logs build clean grafana mlflow ui dev-ui

# Start all services
up:
	docker compose up --build -d

# Stop all services
down:
	docker compose down

# Run unit tests (Rust workspace)
test:
	cargo test --workspace

# Run integration tests via docker compose
test-integration:
	docker compose -f docker-compose.yml -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from integration-tests
	docker compose -f docker-compose.yml -f docker-compose.test.yml down

# View logs
logs:
	docker compose logs -f

# View specific service logs
logs-%:
	docker compose logs -f $*

# Build only
build:
	docker compose build

# Clean everything (volumes, images)
clean:
	docker compose down -v --rmi local

# Open Grafana
grafana:
	open http://localhost:3001

# Open MLFlow
mlflow:
	open http://localhost:5000

# Register LLM provider
register-provider:
	@echo "Usage: make register-provider URL=http://... MODELS=model1,model2"
	curl -X POST http://localhost:8081/v1/providers \
		-H "Content-Type: application/json" \
		-d '{"name":"custom","url":"$(URL)","models":["$(MODELS)"]}'

# Open WebUI
ui:
	open http://localhost:3000

# Dev mode for WebUI (with hot reload)
dev-ui:
	cd webui && npm run dev

# Load test (requires k6)
loadtest:
	k6 run tests/load/llm-router.js
