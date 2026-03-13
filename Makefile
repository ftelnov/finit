.PHONY: up down test logs build

# Start all services
up:
	docker compose --env-file .env up --build -d

# Stop all services
down:
	docker compose down

# Run E2E tests
test:
	docker compose --env-file .env --profile test up --build --abort-on-container-exit e2e-tests

# View logs
logs:
	docker compose logs -f

# View specific agent logs
logs-%:
	docker compose logs -f agent-$*

# Build only
build:
	docker compose build

# Clean everything
clean:
	docker compose down -v --rmi local
