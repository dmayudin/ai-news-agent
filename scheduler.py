#!/usr/bin/env python3
"""
Scheduler для запуска AI News Agent по расписанию
Запускает агента в 9:00, 16:00 и 20:00 МСК каждый день
"""

import schedule
import time
import logging
import os
import sys
from datetime import datetime
from news_agent import NewsAgent

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/ai-news-scheduler.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class NewsScheduler:
    """Scheduler для запуска AI агента по расписанию"""
    
    def __init__(self):
        """Инициализация scheduler"""
        self.openai_key = os.getenv('OPENAI_API_KEY')
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.user_id = os.getenv('TELEGRAM_USER_ID')
        
        if not all([self.openai_key, self.telegram_token, self.user_id]):
            logger.error("Не установлены необходимые переменные окружения")
            raise ValueError("Missing required environment variables")
        
        self.agent = NewsAgent(self.openai_key, self.telegram_token, self.user_id)
        logger.info("NewsScheduler инициализирован")
    
    def job_9am(self):
        """Задача для 9:00 МСК"""
        logger.info("Запуск задачи 9:00 МСК")
        try:
            self.agent.run_full_cycle()
        except Exception as e:
            logger.error(f"Ошибка в задаче 9:00: {e}")
    
    def job_4pm(self):
        """Задача для 16:00 МСК"""
        logger.info("Запуск задачи 16:00 МСК")
        try:
            self.agent.run_full_cycle()
        except Exception as e:
            logger.error(f"Ошибка в задаче 16:00: {e}")
    
    def job_8pm(self):
        """Задача для 20:00 МСК"""
        logger.info("Запуск задачи 20:00 МСК")
        try:
            self.agent.run_full_cycle()
        except Exception as e:
            logger.error(f"Ошибка в задаче 20:00: {e}")
    
    def start(self):
        """Запускает scheduler"""
        logger.info("Запускаю scheduler...")
        
        # Регистрируем задачи
        # Время указано в МСК (UTC+3)
        schedule.every().day.at("09:00").do(self.job_9am)
        schedule.every().day.at("16:00").do(self.job_4pm)
        schedule.every().day.at("20:00").do(self.job_8pm)
        
        logger.info("Задачи зарегистрированы:")
        logger.info("  - 09:00 МСК")
        logger.info("  - 16:00 МСК")
        logger.info("  - 20:00 МСК")
        
        # Основной цикл
        while True:
            try:
                schedule.run_pending()
                time.sleep(60)  # Проверяем каждую минуту
            except KeyboardInterrupt:
                logger.info("Scheduler остановлен пользователем")
                break
            except Exception as e:
                logger.error(f"Ошибка в scheduler: {e}")
                time.sleep(60)


def main():
    """Главная функция"""
    try:
        scheduler = NewsScheduler()
        scheduler.start()
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
