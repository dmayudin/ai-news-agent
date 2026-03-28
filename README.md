# AI News Agent - Telegram Bot для сбора новостей об ИИ

Это полнофункциональный AI агент, который собирает новости об искусственном интеллекте из различных RSS источников, обрабатывает их с помощью OpenAI API и отправляет структурированные сводки в Telegram.

## Возможности

- 📰 **Автоматический сбор новостей** из 5+ RSS источников об ИИ
- 🤖 **AI обработка** новостей через OpenAI API для выделения ключевых моментов
- 📱 **Отправка в Telegram** структурированными сообщениями
- ⏰ **Расписание** - автоматическая отправка в 9:00, 16:00 и 20:00 МСК
- 🔐 **VPN поддержка** для доступа к OpenAI из РФ
- 📊 **Релевантность** - AI оценивает релевантность каждой новости
- 🏷️ **Теги и категории** - автоматическая классификация новостей

## Архитектура

```
┌─────────────────────────────────────────────────────┐
│                   AI News Agent                      │
├─────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────────┐    ┌──────────────┐               │
│  │ RSS Feeds    │    │ Scheduler    │               │
│  │ - OpenAI     │    │ (9:00,16:00, │               │
│  │ - ArXiv      │    │  20:00 МСК)  │               │
│  │ - TechCrunch │    └──────────────┘               │
│  │ - Habr       │                                    │
│  │ - DeepMind   │                                    │
│  └──────────────┘                                    │
│         │                                            │
│         ▼                                            │
│  ┌──────────────────────────────────────────┐       │
│  │      News Processing Pipeline            │       │
│  │  1. Fetch RSS feeds                      │       │
│  │  2. Parse and filter news                │       │
│  │  3. Process with OpenAI                  │       │
│  │  4. Extract key points                   │       │
│  │  5. Format for Telegram                  │       │
│  └──────────────────────────────────────────┘       │
│         │                                            │
│         ▼                                            │
│  ┌──────────────────────────────────────────┐       │
│  │      Telegram Bot                        │       │
│  │  Send formatted messages to user         │       │
│  └──────────────────────────────────────────┘       │
│         │                                            │
│         ▼                                            │
│  ┌──────────────────────────────────────────┐       │
│  │      VPN (WireGuard)                     │       │
│  │  Route traffic through VPN if needed     │       │
│  └──────────────────────────────────────────┘       │
│         │                                            │
│         ▼                                            │
│  ┌──────────────────────────────────────────┐       │
│  │      OpenAI API                          │       │
│  │  Process and analyze news content        │       │
│  └──────────────────────────────────────────┘       │
│                                                      │
└─────────────────────────────────────────────────────┘
```

## Требования

- Python 3.8+
- pip3
- Доступ в интернет (или через VPN)
- OpenAI API ключ
- Telegram Bot Token
- Telegram User ID

## Установка

### 1. Клонирование репозитория

```bash
git clone <repository-url>
cd ai-news-agent
```

### 2. Установка зависимостей

```bash
pip3 install -r requirements.txt
```

### 3. Конфигурация

Скопируйте файл конфигурации и заполните его:

```bash
cp .env.example .env
nano .env
```

Необходимые переменные:
- `OPENAI_API_KEY` - ваш API ключ OpenAI
- `TELEGRAM_BOT_TOKEN` - токен вашего Telegram бота
- `TELEGRAM_USER_ID` - ваш ID в Telegram

### 4. Развертывание на сервере

Для развертывания на сервере Timeweb используйте скрипт:

```bash
sudo bash deploy.sh
```

Этот скрипт:
- Создаст директорию `/opt/ai-news-agent`
- Установит все зависимости
- Создаст systemd сервис
- Настроит автозапуск

### 5. Запуск

После развертывания:

```bash
# Запуск сервиса
sudo systemctl start ai-news-agent

# Проверка статуса
sudo systemctl status ai-news-agent

# Просмотр логов
sudo journalctl -u ai-news-agent -f
```

## Конфигурация WireGuard (для доступа к OpenAI из РФ)

Если вам нужно использовать VPN для доступа к OpenAI:

```bash
# Исправление конфигурации WireGuard
sudo bash fix_wireguard.sh

# Проверка соединения
sudo wg show
```

## RSS Источники

Агент собирает новости из следующих источников:

| Источник | Категория | URL |
|----------|-----------|-----|
| OpenAI Blog | AI News | https://openai.com/blog/rss.xml |
| ArXiv AI | Research | http://arxiv.org/rss/cs.AI |
| TechCrunch | Tech News | https://techcrunch.com/tag/artificial-intelligence/feed/ |
| Хабр | Russian Tech | https://habr.com/ru/rss/hubs/ai/all/ |
| DeepMind | AI News | https://www.deepmind.com/blog/rss.xml |

## Расписание

Агент автоматически отправляет сводки новостей в следующее время (МСК):

- **09:00** - Утренняя сводка
- **16:00** - Дневная сводка
- **20:00** - Вечерняя сводка

## Структура сообщения в Telegram

```
📰 Сводка новостей об ИИ
⏰ 28.03.2026 09:00
==================================================

1. 🔴 Заголовок новости
📌 Источник: OpenAI Blog
🔗 [Читать далее](https://example.com)
📝 Краткое резюме новости...

2. 🟡 Другая новость
...

==================================================
🤖 Сообщение создано AI агентом
```

Где:
- 🔴 - высокая релевантность
- 🟡 - средняя релевантность
- 🟢 - низкая релевантность

## Логирование

Логи сохраняются в:
- `/var/log/ai-news-agent.log` - логи агента
- `/var/log/ai-news-scheduler.log` - логи scheduler

Просмотр логов:
```bash
# Реальное время
sudo journalctl -u ai-news-agent -f

# Последние 100 строк
sudo journalctl -u ai-news-agent -n 100

# За последний час
sudo journalctl -u ai-news-agent --since "1 hour ago"
```

## Отладка

### Проверка конфигурации

```bash
# Проверка переменных окружения
cat /opt/ai-news-agent/.env

# Проверка прав доступа
ls -la /opt/ai-news-agent/

# Проверка логов
tail -f /var/log/ai-news-agent.log
```

### Ручной запуск агента

```bash
# Активируем переменные окружения
source /opt/ai-news-agent/.env

# Запускаем агента напрямую
python3 /opt/ai-news-agent/news_agent.py
```

### Проверка WireGuard

```bash
# Статус соединения
sudo wg show

# Проверка маршрутов
sudo ip route show

# Тест пинга
ping -c 3 8.8.8.8

# Тест доступа к OpenAI
curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"
```

## Структура файлов

```
ai-news-agent/
├── news_agent.py           # Основной модуль агента
├── scheduler.py            # Scheduler для расписания
├── requirements.txt        # Зависимости Python
├── .env.example           # Пример конфигурации
├── ai-news-agent.service  # Systemd сервис
├── deploy.sh              # Скрипт развертывания
├── fix_wireguard.sh       # Скрипт исправления WireGuard
└── README.md              # Этот файл
```

## Переменные окружения

```bash
# OpenAI API Key
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxx

# Telegram Bot Token
TELEGRAM_BOT_TOKEN=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh

# Telegram User ID (для отправки личных сообщений)
TELEGRAM_USER_ID=123456789
```

## Возможные проблемы и решения

### Проблема: SSH соединение разрывается после установки WireGuard

**Решение:**
```bash
sudo bash fix_wireguard.sh
```

Это исправит конфигурацию WireGuard так, чтобы она не маршрутизировала весь трафик через VPN.

### Проблема: Агент не отправляет сообщения

**Проверка:**
1. Убедитесь, что переменные окружения установлены: `cat /opt/ai-news-agent/.env`
2. Проверьте логи: `sudo journalctl -u ai-news-agent -f`
3. Проверьте доступ к OpenAI: `curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"`
4. Проверьте токен Telegram: `curl https://api.telegram.org/bot<TOKEN>/getMe`

### Проблема: Высокое использование памяти

**Решение:** Уменьшите количество статей, обрабатываемых за раз, отредактировав `news_agent.py`:
```python
for entry in feed.entries[:5]:  # Вместо [:10]
```

## Безопасность

- Переменные окружения хранятся в `/opt/ai-news-agent/.env` с правами 644
- Логи хранятся в `/var/log/` с правами 644
- Сервис запускается от root (можно изменить на другого пользователя)
- API ключи не логируются в полном виде

## Лицензия

MIT

## Автор

Создано для dmadyudin

## Поддержка

Для проблем и вопросов создавайте issues в репозитории.

## Дополнительные ресурсы

- [OpenAI API Documentation](https://platform.openai.com/docs)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [WireGuard Documentation](https://www.wireguard.com/)
- [feedparser Documentation](https://feedparser.readthedocs.io/)
