#!/usr/bin/env python3
"""
Mini App сервер — Flask API для Telegram WebApp.
Отдаёт новости в JSON и статические файлы.
"""
import os
import sys
import logging
from datetime import datetime

from flask import Flask, jsonify, send_from_directory
from dotenv import load_dotenv

load_dotenv('/opt/ai-news-agent/.env')
sys.path.insert(0, '/opt/ai-news-agent')

from news_agent import NewsAgent

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='')

agent = NewsAgent(
    openai_api_key=os.getenv('OPENAI_API_KEY', ''),
    telegram_token=os.getenv('TELEGRAM_BOT_TOKEN', ''),
    user_id=os.getenv('TELEGRAM_USER_ID', ''),
)

# Кэш новостей
_news_cache = {'data': [], 'updated': None}
CACHE_TTL = 1800  # 30 минут


def get_cached_news():
    now = datetime.now().timestamp()
    if not _news_cache['data'] or (now - (_news_cache['updated'] or 0)) > CACHE_TTL:
        logger.info("Обновляем кэш новостей...")
        news = agent.fetch_news(hours_back=24) or agent.fetch_news(hours_back=72)
        _news_cache['data'] = news
        _news_cache['updated'] = now
    return _news_cache['data']


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/news')
def api_news():
    try:
        news = get_cached_news()
        items = []
        for item in news[:30]:
            items.append({
                'title':     item.get('title', ''),
                'link':      item.get('link', ''),
                'source':    item.get('source', ''),
                'published': item.get('published', ''),
                'summary':   item.get('summary', '')[:300] if item.get('summary') else '',
            })
        return jsonify({
            'ok': True,
            'count': len(items),
            'updated': _news_cache.get('updated'),
            'items': items,
        })
    except Exception as e:
        logger.error(f"api_news error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/analyze')
def api_analyze():
    try:
        news = get_cached_news()
        if not news:
            return jsonify({'ok': False, 'error': 'Новостей не найдено'}), 404
        analysis = agent.analyze_with_ai(news[:15])
        return jsonify({'ok': True, 'analysis': analysis})
    except Exception as e:
        logger.error(f"api_analyze error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/health')
def health():
    return jsonify({'ok': True, 'service': 'ai-news-webapp'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
