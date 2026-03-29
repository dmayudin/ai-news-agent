#!/bin/bash
# ============================================================
# deploy.sh — Деплой AI News Agent в Docker Compose
# Запуск: bash deploy.sh
# ============================================================

set -e

APP_DIR="/opt/ai-news-agent"
COMPOSE_FILE="$APP_DIR/docker-compose.yml"

echo "=== AI News Agent — Docker Deploy ==="
echo "Директория: $APP_DIR"
echo ""

# 1. Установка Docker, если не установлен
if ! command -v docker &>/dev/null; then
  echo "[1/6] Устанавливаю Docker..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker
  systemctl start docker
  echo "Docker установлен: $(docker --version)"
else
  echo "[1/6] Docker уже установлен: $(docker --version)"
fi

# 2. Установка Docker Compose plugin, если не установлен
if ! docker compose version &>/dev/null; then
  echo "[2/6] Устанавливаю Docker Compose plugin..."
  apt-get install -y docker-compose-plugin
else
  echo "[2/6] Docker Compose уже установлен: $(docker compose version)"
fi

# 3. Остановка старых systemd-сервисов
echo "[3/6] Останавливаю старые systemd-сервисы..."
systemctl stop ai-news-agent.service 2>/dev/null && echo "  ai-news-agent.service остановлен" || echo "  ai-news-agent.service не запущен"
systemctl stop ai-news-bot.service   2>/dev/null && echo "  ai-news-bot.service остановлен"   || echo "  ai-news-bot.service не запущен"
systemctl disable ai-news-agent.service 2>/dev/null || true
systemctl disable ai-news-bot.service   2>/dev/null || true

# 4. Сборка образов
echo "[4/6] Собираю Docker-образы..."
cd "$APP_DIR"
docker compose build --no-cache

# 5. Запуск контейнеров
echo "[5/6] Запускаю контейнеры..."
docker compose up -d

# 6. Проверка статуса
echo "[6/6] Проверяю статус..."
sleep 3
docker compose ps
echo ""
echo "=== Логи (последние 20 строк) ==="
docker compose logs --tail=20
echo ""
echo "=== Деплой завершён ==="
echo "Управление:"
echo "  docker compose logs -f         -- следить за логами"
echo "  docker compose ps              -- статус контейнеров"
echo "  docker compose restart bot     -- перезапустить бот"
echo "  docker compose restart agent   -- перезапустить агент"
echo "  docker compose down            -- остановить всё"
