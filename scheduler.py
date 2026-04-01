#!/usr/bin/env python3
"""Scheduler для запуска AI News Agent по расписанию (9:00, 16:00, 20:00 МСК)
   + обработка очереди отложенных публикаций из Mini App."""

import schedule
import time
import logging
import os
import sys
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ['TZ'] = 'Europe/Moscow'
time.tzset()

from dotenv import load_dotenv
load_dotenv('/opt/ai-news-agent/.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/ai-news-scheduler.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

sys.path.insert(0, '/opt/ai-news-agent')
from news_agent import NewsAgent

# Путь к файлу очереди (shared-data volume, тот же что в webapp/app.py)
SCHEDULE_FILE = '/opt/ai-news-agent/data/scheduled_posts.json'
BOT_TOKEN  = os.getenv('TELEGRAM_BOT_TOKEN', '')

def create_agent():
    return NewsAgent(
        openai_api_key=os.getenv('OPENAI_API_KEY'),
        telegram_token=BOT_TOKEN,
        user_id=os.getenv('TELEGRAM_USER_ID')
    )

# ── Обработка очереди отложенных публикаций ────────────────────────────────────
def _load_tasks():
    try:
        if os.path.exists(SCHEDULE_FILE):
            with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning('_load_tasks error: %s', e)
    return []

def _save_tasks(tasks):
    try:
        os.makedirs(os.path.dirname(SCHEDULE_FILE), exist_ok=True)
        with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error('_save_tasks error: %s', e)

def _send_to_channel(text: str, channel_id: str) -> bool:
    if not BOT_TOKEN:
        logger.error('BOT_TOKEN not set, cannot send scheduled post')
        return False
    try:
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
        resp = requests.post(url, json={
            'chat_id':                  channel_id,
            'text':                     text,
            'parse_mode':               'HTML',
            'disable_web_page_preview': True,
        }, timeout=15)
        data = resp.json()
        if data.get('ok'):
            logger.info('Scheduled post sent to %s (msg_id=%s)', channel_id, data['result']['message_id'])
            return True
        else:
            logger.error('Telegram error: %s', data.get('description'))
            return False
    except Exception as e:
        logger.error('_send_to_channel error: %s', e)
        return False

def process_scheduled_posts():
    """Проверяем очередь и отправляем посты у которых наступило время."""
    tasks = _load_tasks()
    if not tasks:
        return

    try:
        msk = ZoneInfo('Europe/Moscow')
        now = datetime.now(msk)
    except Exception:
        now = datetime.now()

    changed = False
    for task in tasks:
        if task.get('status') != 'pending':
            continue
        try:
            publish_at = datetime.fromisoformat(task['publish_at'])
            if publish_at.tzinfo is None:
                publish_at = publish_at.replace(tzinfo=ZoneInfo('Europe/Moscow'))
        except Exception as e:
            logger.warning('Invalid publish_at for task %s: %s', task.get('id'), e)
            task['status'] = 'error'
            changed = True
            continue

        if now >= publish_at:
            logger.info('Processing scheduled task %s (publish_at=%s)', task.get('id'), publish_at)
            ok = _send_to_channel(task['text'], task.get('channel_id', '@ai_is_you'))
            task['status'] = 'sent' if ok else 'error'
            task['processed_at'] = now.isoformat()
            changed = True

    if changed:
        _save_tasks(tasks)

def run_job(label: str):
    logger.info(f"=== Запуск задачи: {label} ===")
    try:
        agent = create_agent()
        agent.run()
    except Exception as e:
        logger.error(f"Ошибка в задаче {label}: {e}")

# Расписание по МСК
schedule.every().day.at("09:00").do(run_job, "09:00 МСК")
schedule.every().day.at("16:00").do(run_job, "16:00 МСК")
schedule.every().day.at("20:00").do(run_job, "20:00 МСК")

logger.info("Scheduler запущен. Расписание: 09:00, 16:00, 20:00 МСК")
logger.info("Обработка очереди отложенных публикаций: каждые 30 секунд")
logger.info(f"Текущее время МСК: {time.strftime('%H:%M')}")

while True:
    try:
        schedule.run_pending()
        process_scheduled_posts()
        time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Scheduler остановлен")
        break
    except Exception as e:
        logger.error(f"Ошибка scheduler: {e}")
        time.sleep(60)
