#!/usr/bin/env python3
"""
AI News Agent - собирает новости по RSS и обрабатывает их через OpenAI
"""

import feedparser
import os
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from openai import OpenAI
import requests
from urllib.parse import urljoin

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/ai-news-agent.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# RSS источники для сбора новостей об ИИ
RSS_FEEDS = [
    {
        'name': 'OpenAI Blog',
        'url': 'https://openai.com/blog/rss.xml',
        'category': 'AI News'
    },
    {
        'name': 'ArXiv AI',
        'url': 'http://arxiv.org/rss/cs.AI',
        'category': 'Research'
    },
    {
        'name': 'TechCrunch AI',
        'url': 'https://techcrunch.com/tag/artificial-intelligence/feed/',
        'category': 'Tech News'
    },
    {
        'name': 'Хабр AI',
        'url': 'https://habr.com/ru/rss/hubs/ai/all/',
        'category': 'Russian Tech'
    },
    {
        'name': 'DeepMind Blog',
        'url': 'https://www.deepmind.com/blog/rss.xml',
        'category': 'AI News'
    }
]

class NewsAgent:
    """AI агент для сбора и обработки новостей"""
    
    def __init__(self, openai_api_key: str, telegram_token: str, user_id: str):
        """
        Инициализация агента
        
        Args:
            openai_api_key: API ключ OpenAI
            telegram_token: Токен Telegram бота
            user_id: ID пользователя Telegram для отправки сообщений
        """
        self.client = OpenAI(api_key=openai_api_key)
        self.telegram_token = telegram_token
        self.user_id = user_id
        self.news_cache = {}
        logger.info("NewsAgent инициализирован")
    
    def fetch_news(self, hours_back: int = 24) -> List[Dict]:
        """
        Собирает новости из RSS источников
        
        Args:
            hours_back: Количество часов в прошлое для сбора новостей
            
        Returns:
            Список новостей
        """
        news_items = []
        cutoff_time = datetime.utcnow() - timedelta(hours=hours_back)
        
        for feed_config in RSS_FEEDS:
            try:
                logger.info(f"Загружаю {feed_config['name']}...")
                feed = feedparser.parse(feed_config['url'])
                
                for entry in feed.entries[:10]:  # Берем последние 10 статей
                    try:
                        # Парсим дату публикации
                        pub_date = None
                        if hasattr(entry, 'published_parsed') and entry.published_parsed:
                            pub_date = datetime(*entry.published_parsed[:6])
                        
                        # Пропускаем старые новости
                        if pub_date and pub_date < cutoff_time:
                            continue
                        
                        news_item = {
                            'title': entry.get('title', 'No title'),
                            'link': entry.get('link', ''),
                            'summary': entry.get('summary', '')[:500],  # Первые 500 символов
                            'published': pub_date.isoformat() if pub_date else datetime.utcnow().isoformat(),
                            'source': feed_config['name'],
                            'category': feed_config['category']
                        }
                        news_items.append(news_item)
                        
                    except Exception as e:
                        logger.warning(f"Ошибка при обработке записи: {e}")
                        continue
                        
            except Exception as e:
                logger.error(f"Ошибка при загрузке {feed_config['name']}: {e}")
                continue
        
        logger.info(f"Собрано {len(news_items)} новостей")
        return news_items
    
    def process_with_ai(self, news_items: List[Dict]) -> List[Dict]:
        """
        Обрабатывает новости через OpenAI для выделения ключевых моментов
        
        Args:
            news_items: Список новостей
            
        Returns:
            Обработанные новости с анализом
        """
        processed_news = []
        
        for item in news_items:
            try:
                # Создаем промпт для анализа новости
                prompt = f"""Проанализируй следующую новость об ИИ и предоставь:
1. Краткое резюме (1-2 предложения)
2. Ключевые моменты (3-5 пунктов)
3. Релевантность для ИИ (высокая/средняя/низкая)
4. Рекомендуемые теги

Заголовок: {item['title']}
Источник: {item['source']}
Текст: {item['summary']}

Ответь в формате JSON."""

                response = self.client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=[
                        {"role": "system", "content": "Ты эксперт в области искусственного интеллекта. Анализируй новости и выделяй самое важное."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=500
                )
                
                # Парсим ответ
                analysis_text = response.choices[0].message.content
                
                # Пытаемся распарсить JSON
                try:
                    analysis = json.loads(analysis_text)
                except json.JSONDecodeError:
                    # Если JSON невалидный, используем текст как есть
                    analysis = {
                        "summary": analysis_text,
                        "relevance": "medium",
                        "tags": ["AI", "News"]
                    }
                
                # Добавляем анализ к новости
                processed_item = {**item, "analysis": analysis}
                processed_news.append(processed_item)
                
                logger.info(f"Обработана новость: {item['title'][:50]}...")
                
            except Exception as e:
                logger.error(f"Ошибка при обработке новости через AI: {e}")
                # Добавляем новость без анализа
                processed_item = {**item, "analysis": {"summary": item['summary']}}
                processed_news.append(processed_item)
        
        return processed_news
    
    def format_telegram_message(self, news_items: List[Dict]) -> str:
        """
        Форматирует новости для отправки в Telegram
        
        Args:
            news_items: Список обработанных новостей
            
        Returns:
            Отформатированное сообщение
        """
        message = "📰 **Сводка новостей об ИИ**\n"
        message += f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        message += "=" * 50 + "\n\n"
        
        for i, item in enumerate(news_items, 1):
            analysis = item.get('analysis', {})
            relevance = analysis.get('relevance', 'medium').upper()
            
            # Иконка релевантности
            relevance_icon = {
                'ВЫСОКАЯ': '🔴',
                'HIGH': '🔴',
                'СРЕДНЯЯ': '🟡',
                'MEDIUM': '🟡',
                'НИЗКАЯ': '🟢',
                'LOW': '🟢'
            }.get(relevance, '⚪')
            
            message += f"{i}. {relevance_icon} **{item['title']}**\n"
            message += f"📌 Источник: {item['source']}\n"
            message += f"🔗 [Читать далее]({item['link']})\n"
            
            if 'summary' in analysis:
                summary = analysis['summary']
                if isinstance(summary, str):
                    message += f"📝 {summary[:200]}...\n"
            
            message += "\n"
        
        message += "=" * 50 + "\n"
        message += "🤖 Сообщение создано AI агентом"
        
        return message
    
    def send_telegram_message(self, message: str) -> bool:
        """
        Отправляет сообщение в Telegram
        
        Args:
            message: Текст сообщения
            
        Returns:
            True если успешно, False если ошибка
        """
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {
                "chat_id": self.user_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            
            response = requests.post(url, data=data, timeout=10)
            
            if response.status_code == 200:
                logger.info("Сообщение успешно отправлено в Telegram")
                return True
            else:
                logger.error(f"Ошибка при отправке в Telegram: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
            return False
    
    def run_full_cycle(self) -> bool:
        """
        Запускает полный цикл: сбор -> обработка -> отправка
        
        Returns:
            True если успешно, False если ошибка
        """
        try:
            logger.info("Начинаю цикл сбора и обработки новостей...")
            
            # Собираем новости
            news = self.fetch_news(hours_back=24)
            
            if not news:
                logger.warning("Новостей не найдено")
                return False
            
            # Обрабатываем через AI
            processed_news = self.process_with_ai(news)
            
            # Форматируем сообщение
            message = self.format_telegram_message(processed_news)
            
            # Отправляем в Telegram
            success = self.send_telegram_message(message)
            
            logger.info("Цикл завершен успешно")
            return success
            
        except Exception as e:
            logger.error(f"Ошибка в полном цикле: {e}")
            return False


if __name__ == "__main__":
    # Получаем переменные окружения
    openai_key = os.getenv('OPENAI_API_KEY')
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
    user_id = os.getenv('TELEGRAM_USER_ID')
    
    if not all([openai_key, telegram_token, user_id]):
        logger.error("Не установлены необходимые переменные окружения")
        exit(1)
    
    # Создаем и запускаем агента
    agent = NewsAgent(openai_key, telegram_token, user_id)
    agent.run_full_cycle()
