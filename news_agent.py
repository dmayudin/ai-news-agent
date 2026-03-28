#!/usr/bin/env python3
"""AI News Agent - собирает новости по RSS и обрабатывает через OpenAI"""

import feedparser
import os
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict
from openai import OpenAI
import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/ai-news-agent.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

RSS_FEEDS = [
    {'name': 'ArXiv AI',        'url': 'http://arxiv.org/rss/cs.AI',                               'category': 'Research'},
    {'name': 'TechCrunch AI',   'url': 'https://techcrunch.com/tag/artificial-intelligence/feed/', 'category': 'Tech'},
    {'name': 'VentureBeat AI',  'url': 'https://venturebeat.com/category/ai/feed/',                'category': 'Business'},
    {'name': 'The Decoder',     'url': 'https://the-decoder.com/feed/',                            'category': 'AI News'},
    {'name': 'MIT Tech Review', 'url': 'https://www.technologyreview.com/feed/',                   'category': 'Research'},
    {'name': 'Хабр ML',         'url': 'https://habr.com/ru/rss/hubs/machine_learning/all/',       'category': 'RU Tech'},
    {'name': 'OpenAI Blog',     'url': 'https://openai.com/blog/rss.xml',                          'category': 'OpenAI'},
]


def html_escape(text: str) -> str:
    """Экранирует спецсимволы HTML для Telegram"""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def source_link(url: str) -> str:
    """Создаёт HTML гиперссылку вида ➡️ Источник"""
    return f'<a href="{url}">➡️ Источник</a>'


class NewsAgent:
    def __init__(self, openai_api_key: str, telegram_token: str, user_id: str):
        self.client = OpenAI(api_key=openai_api_key)
        self.telegram_token = telegram_token
        self.user_id = user_id
        logger.info("NewsAgent инициализирован")

    def fetch_news(self, hours_back: int = 8) -> List[Dict]:
        news_items = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)

        for feed_config in RSS_FEEDS:
            try:
                logger.info(f"Загружаю {feed_config['name']}...")
                import socket
                old_timeout = socket.getdefaulttimeout()
                socket.setdefaulttimeout(10)
                try:
                    feed = feedparser.parse(feed_config['url'])
                finally:
                    socket.setdefaulttimeout(old_timeout)

                if not feed.entries:
                    logger.warning(f"Нет записей в {feed_config['name']}")
                    continue

                count = 0
                for entry in feed.entries[:10]:
                    try:
                        pub_date = None
                        for attr in ['published_parsed', 'updated_parsed']:
                            if hasattr(entry, attr) and getattr(entry, attr):
                                t = getattr(entry, attr)
                                pub_date = datetime(*t[:6], tzinfo=timezone.utc)
                                break

                        if pub_date and pub_date < cutoff_time:
                            continue

                        summary = entry.get('summary', '') or entry.get('description', '')
                        summary = re.sub('<[^<]+?>', '', summary)[:400].strip()

                        news_item = {
                            'title': entry.get('title', 'No title')[:150],
                            'link': entry.get('link', ''),
                            'summary': summary,
                            'published': pub_date.strftime('%d.%m %H:%M') if pub_date else '',
                            'source': feed_config['name'],
                            'category': feed_config['category']
                        }
                        news_items.append(news_item)
                        count += 1
                    except Exception as e:
                        logger.warning(f"Ошибка записи в {feed_config['name']}: {e}")
                        continue

                logger.info(f"  -> {count} новостей из {feed_config['name']}")

            except Exception as e:
                logger.error(f"Ошибка загрузки {feed_config['name']}: {e}")
                continue

        logger.info(f"Итого собрано: {len(news_items)} новостей")
        return news_items

    def analyze_with_ai(self, news_items: List[Dict]) -> str:
        if not news_items:
            return "Новостей за период не найдено."

        news_text = ""
        for i, item in enumerate(news_items[:20], 1):
            news_text += f"{i}. [{item['source']}] {item['title']}\n"
            if item['summary']:
                news_text += f"   {item['summary'][:200]}\n"
            news_text += "\n"

        prompt = f"""Ты AI-аналитик. Проанализируй следующие новости об искусственном интеллекте и создай структурированную сводку на русском языке.

НОВОСТИ:
{news_text}

Создай сводку в следующем формате:

ТОП-3 ГЛАВНЫХ СОБЫТИЯ:
(3 самые важные новости с кратким объяснением почему они важны)

ТРЕНДЫ:
(2-3 ключевых тренда которые прослеживаются в новостях)

ИНТЕРЕСНЫЕ ФАКТЫ:
(2-3 интересных факта или открытия)

Будь конкретным, избегай воды. Каждый пункт — 1-2 предложения максимум."""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "Ты эксперт-аналитик в области ИИ. Пишешь кратко, по делу, на русском языке."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=800
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Ошибка OpenAI: {e}")
            return f"Не удалось получить AI-анализ: {e}"

    def send_telegram(self, text: str, chat_id: str = None, parse_mode: str = "HTML") -> bool:
        """Отправляет сообщение в Telegram с поддержкой HTML"""
        target = chat_id or self.user_id
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {
                "chat_id": target,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True
            }
            response = requests.post(url, data=data, timeout=15)
            if response.status_code == 200:
                logger.info("Сообщение отправлено в Telegram")
                return True
            else:
                logger.error(f"Telegram ошибка {response.status_code}: {response.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
            return False

    def format_and_send(self, news_items: List[Dict], ai_analysis: str, chat_id: str = None):
        now = datetime.now().strftime('%d.%m.%Y %H:%M')

        # Сообщение 1: AI анализ (экранируем текст от GPT, т.к. он может содержать < > &)
        msg1 = f"<b>🤖 AI-СВОДКА НОВОСТЕЙ ОБ ИИ</b>\n"
        msg1 += f"<i>Время: {now} МСК</i>\n"
        msg1 += "─" * 28 + "\n\n"
        msg1 += html_escape(ai_analysis)
        self.send_telegram(msg1, chat_id=chat_id)

        # Сообщение 2: Список новостей с гиперссылками ➡️ Источник
        msg2 = f"<b>📋 НОВОСТИ ЗА ПЕРИОД ({len(news_items)} шт.):</b>\n\n"
        for i, item in enumerate(news_items[:15], 1):
            title = html_escape(item['title'])
            src   = html_escape(item['source'])
            link  = item['link']
            date  = item['published']

            entry  = f"{i}. <b>{title}</b>\n"
            entry += f"   <i>{src}"
            if date:
                entry += f" · {date}"
            entry += f"</i>  {source_link(link)}\n\n"

            if len(msg2) + len(entry) > 3800:
                self.send_telegram(msg2, chat_id=chat_id)
                msg2 = ""

            msg2 += entry

        if msg2:
            self.send_telegram(msg2, chat_id=chat_id)

    def run(self) -> bool:
        logger.info("=== Запуск цикла сбора новостей ===")

        news = self.fetch_news(hours_back=8)

        if not news:
            logger.warning("Новостей не найдено, пробуем за 48 часов...")
            news = self.fetch_news(hours_back=48)

        if not news:
            logger.warning("Новостей не найдено вообще")
            self.send_telegram("😔 Новостей об ИИ за последние 48 часов не найдено.")
            return False

        logger.info(f"Анализирую {len(news)} новостей через OpenAI...")
        ai_analysis = self.analyze_with_ai(news)

        self.format_and_send(news, ai_analysis)
        logger.info("=== Цикл завершен успешно ===")
        return True


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv('/opt/ai-news-agent/.env')

    openai_key = os.getenv('OPENAI_API_KEY')
    tg_token   = os.getenv('TELEGRAM_BOT_TOKEN')
    user_id    = os.getenv('TELEGRAM_USER_ID')

    if not all([openai_key, tg_token, user_id]):
        logger.error("Не установлены переменные окружения")
        exit(1)

    agent = NewsAgent(openai_key, tg_token, user_id)
    agent.run()
