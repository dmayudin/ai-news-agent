#!/usr/bin/env python3
"""Scheduler для запуска AI News Agent по расписанию (9:00, 16:00, 20:00 МСК)"""

import schedule
import time
import logging
import os
import sys

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

def create_agent():
    return NewsAgent(
        openai_api_key=os.getenv('OPENAI_API_KEY'),
        telegram_token=os.getenv('TELEGRAM_BOT_TOKEN'),
        user_id=os.getenv('TELEGRAM_USER_ID')
    )

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
logger.info(f"Текущее время МСК: {time.strftime('%H:%M')}")

while True:
    try:
        schedule.run_pending()
        time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Scheduler остановлен")
        break
    except Exception as e:
        logger.error(f"Ошибка scheduler: {e}")
        time.sleep(60)
