# Makefile for ebooks-manager dev workflow

# Default target
.PHONY: rebuild
rebuild:
	@echo "🔨 Rebuilding ebooks-manager (normal mode)..."
	@./docker_rebuild.sh

.PHONY: debug
debug:
	@echo "🐞 Rebuilding ebooks-manager (debug mode)..."
	@./docker_rebuild.sh --debug

.PHONY: up
up:
	@echo "🚀 Starting ebooks-manager without rebuild..."
	@docker compose -f /mnt/data/docker/docker-scripts/docker-compose.yml \
	               -f .devcontainer/docker-compose.override.yml \
	               up -d ebooks-manager

.PHONY: down
down:
	@echo "🛑 Stopping ebooks-manager..."
	@docker compose -f /mnt/data/docker/docker-scripts/docker-compose.yml \
	               -f .devcontainer/docker-compose.override.yml \
	               down ebooks-manager

.PHONY: logs
logs:
	@docker compose -f /mnt/data/docker/docker-scripts/docker-compose.yml \
	               -f .devcontainer/docker-compose.override.yml \
	               logs -f ebooks-manager
