.PHONY: up down stop test test-unit test-integration lint deploy rollback logs build clean grafana mlflow ui dev-ui eval eval-local eval-l1 eval-l2

# Start all services
up:
	docker compose up --build -d

# Stop all services (immediate)
down:
	docker compose down

# Graceful shutdown (ordered drain)
stop:
	docker compose stop webui
	docker compose stop orchestrator
	docker compose stop worker reviewer planner bootstrapper
	docker compose stop llm-router
	docker compose stop otel-collector prometheus alertmanager grafana mlflow
	docker compose stop postgres

# Lint all code
lint:
	cd orchestrator && cargo clippy --all-targets -- -D warnings
	cd llm-router && cargo clippy --all-targets -- -D warnings
	cd orchestrator && cargo fmt --check
	cd llm-router && cargo fmt --check
	cd agents && python -m ruff check .
	cd agents && python -m ruff format --check .
	cd webui && npm run lint

# Run unit tests (Rust workspace)
test: test-unit

test-unit:
	cargo test --workspace

# Run integration tests via docker compose
test-integration:
	docker compose -f docker-compose.yml -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from integration-tests
	docker compose -f docker-compose.yml -f docker-compose.test.yml down

# Deploy (build + start + verify health)
deploy:
	docker compose up --build -d --remove-orphans
	@echo "Waiting for services to become healthy..."
	@sleep 5
	@docker compose exec orchestrator wget -q --spider http://localhost:8080/health && echo "orchestrator: healthy" || echo "orchestrator: UNHEALTHY"
	@docker compose exec llm-router wget -q --spider http://localhost:8081/health && echo "llm-router: healthy" || echo "llm-router: UNHEALTHY"

# Rollback to previous version
rollback:
	docker compose down
	git checkout HEAD~1 -- docker-compose.yml
	docker compose up --build -d

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

# === Eval with real LLM ===

# Run full eval suite (requires eval stack running)
eval:
	docker compose -f docker-compose.eval.yml up -d
	cd evals && JWT_SECRET=dev-secret pytest -v --tb=short --timeout=300 \
		test_smoke_llm.py test_rule_compliance.py test_judged_pipeline.py \
		test_env_challenge.py test_complex_env.py test_continuous.py

# Run eval locally (agents + router must be running)
eval-local:
	cd evals && JWT_SECRET=dev-secret LLM_MODEL=/opt/MiniMaxAI/MiniMax-M2.7 \
		pytest -v --tb=short -s --timeout=300

# Run only smoke tests (LLM reachability)
eval-smoke:
	cd evals && LLM_URL=http://10.70.2.11:8006 LLM_MODEL=/opt/MiniMaxAI/MiniMax-M2.7 \
		pytest -v --tb=short -s test_smoke_llm.py

# Run only rule compliance tests
eval-rules:
	cd evals && JWT_SECRET=dev-secret pytest -v --tb=short -s --timeout=300 \
		test_rule_compliance.py
