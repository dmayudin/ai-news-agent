#!/bin/bash
# Скрипт развертывания AI News Agent на сервере

set -e

echo "=== Развертывание AI News Agent ==="

# Проверка прав администратора
if [[ $EUID -ne 0 ]]; then
   echo "Этот скрипт должен быть запущен от root"
   exit 1
fi

# Создание директории приложения
echo "1. Создание директории приложения..."
mkdir -p /opt/ai-news-agent
cd /opt/ai-news-agent

# Копирование файлов
echo "2. Копирование файлов приложения..."
cp news_agent.py /opt/ai-news-agent/
cp scheduler.py /opt/ai-news-agent/
cp requirements.txt /opt/ai-news-agent/
cp ai-news-agent.service /etc/systemd/system/

# Создание .env файла
echo "3. Создание конфигурационного файла..."
if [ ! -f /opt/ai-news-agent/.env ]; then
    cp .env.example /opt/ai-news-agent/.env
    echo "⚠️  ВАЖНО: Отредактируйте /opt/ai-news-agent/.env и добавьте ваши API ключи"
else
    echo "✓ Файл .env уже существует"
fi

# Установка зависимостей Python
echo "4. Установка зависимостей Python..."
pip3 install -r /opt/ai-news-agent/requirements.txt

# Создание директории для логов
echo "5. Создание директории для логов..."
mkdir -p /var/log/ai-news-agent
touch /var/log/ai-news-agent.log
touch /var/log/ai-news-scheduler.log
chmod 644 /var/log/ai-news-agent.log
chmod 644 /var/log/ai-news-scheduler.log

# Установка прав доступа
echo "6. Установка прав доступа..."
chmod 755 /opt/ai-news-agent/scheduler.py
chmod 755 /opt/ai-news-agent/news_agent.py
chmod 644 /opt/ai-news-agent/.env
chmod 644 /etc/systemd/system/ai-news-agent.service

# Перезагрузка systemd
echo "7. Перезагрузка systemd..."
systemctl daemon-reload

# Включение сервиса при загрузке
echo "8. Включение сервиса при загрузке..."
systemctl enable ai-news-agent.service

echo ""
echo "=== Развертывание завершено ==="
echo ""
echo "Следующие шаги:"
echo "1. Отредактируйте /opt/ai-news-agent/.env и добавьте ваши API ключи"
echo "2. Запустите сервис: systemctl start ai-news-agent"
echo "3. Проверьте статус: systemctl status ai-news-agent"
echo "4. Просмотрите логи: journalctl -u ai-news-agent -f"
echo ""
