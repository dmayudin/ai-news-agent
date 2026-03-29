#!/usr/bin/env python3
"""
Mini App сервер — Flask API для Telegram WebApp.
Отдаёт новости в JSON и статические файлы.
"""
import os
import sys
import logging
from datetime import datetime

from flask import Flask, jsonify, send_from_directory, request
from dotenv import load_dotenv

load_dotenv('/opt/ai-news-agent/.env')
sys.path.insert(0, '/opt/ai-news-agent')

from news_agent import NewsAgent

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='')

# CORS для Telegram WebApp
@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

agent = NewsAgent(
    openai_api_key=os.getenv('OPENAI_API_KEY', ''),
    telegram_token=os.getenv('TELEGRAM_BOT_TOKEN', ''),
    user_id=os.getenv('TELEGRAM_USER_ID', ''),
)

# Кэш новостей
_news_cache = {'data': [], 'updated': None}
CACHE_TTL = 1800  # 30 минут


def get_cached_news(force_refresh: bool = False):
    now = datetime.now().timestamp()
    expired = not _news_cache['data'] or (now - (_news_cache['updated'] or 0)) > CACHE_TTL
    if expired or force_refresh:
        logger.info("Обновляем кэш новостей (force=%s)...", force_refresh)
        news = agent.fetch_news(hours_back=24)
        if not news:
            news = agent.fetch_news(hours_back=72)
        _news_cache['data'] = news or []
        _news_cache['updated'] = now
    return _news_cache['data']


def format_item(item):
    """Нормализуем поля новости для API."""
    published = item.get('published', '') or ''
    # Пробуем привести дату к ISO формату для JS Date()
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(published)
        published = dt.isoformat()
    except Exception:
        pass  # оставляем как есть

    return {
        'title':     (item.get('title') or '').strip(),
        'link':      item.get('link', '') or '',
        'source':    (item.get('source') or '').strip(),
        'published': published,
        'summary':   (item.get('summary') or '')[:400].strip(),
    }


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/news')
def api_news():
    try:
        force = request.args.get('refresh') == '1'
        news  = get_cached_news(force_refresh=force)
        items = [format_item(n) for n in news[:50]]
        return jsonify({
            'ok':      True,
            'count':   len(items),
            'updated': _news_cache.get('updated'),
            'items':   items,
        })
    except Exception as e:
        logger.error("api_news error: %s", e, exc_info=True)
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
        logger.error("api_analyze error: %s", e, exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/health')
def health():
    cached = len(_news_cache.get('data') or [])
    updated = _news_cache.get('updated')
    return jsonify({
        'ok':           True,
        'service':      'ai-news-webapp',
        'cached_count': cached,
        'updated':      updated,
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
