#!/usr/bin/env python3
"""
Mini App сервер — Flask API для Telegram WebApp.
Отдаёт новости в JSON (с переводом на русский) и статические файлы.
"""
import os
import sys
import json
import logging
from datetime import datetime

from flask import Flask, jsonify, send_from_directory, request
from dotenv import load_dotenv

load_dotenv('/opt/ai-news-agent/.env')
sys.path.insert(0, '/opt/ai-news-agent')

from news_agent import NewsAgent
from llm_client import chat_complete

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='')


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

# ── Кэш новостей ──────────────────────────────────────────────────────────────
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


# ── Пакетный перевод через GPT ────────────────────────────────────────────────
def _is_russian(text: str) -> bool:
    """Проверяем, написан ли текст в основном на русском."""
    if not text:
        return True
    ru_chars = sum(1 for c in text if '\u0400' <= c <= '\u04ff')
    return ru_chars / max(len(text), 1) > 0.3


def translate_batch(items: list) -> list:
    """
    Переводит заголовки и описания пакетно через GPT.
    Возвращает список словарей с ключами title_ru и summary_ru.
    Тексты, уже написанные на русском, не переводятся.
    """
    # Собираем индексы, которые нужно перевести
    to_translate = []
    for i, item in enumerate(items):
        title   = item.get('title', '') or ''
        summary = item.get('summary', '') or ''
        if not _is_russian(title) or not _is_russian(summary):
            to_translate.append(i)

    if not to_translate:
        logger.info("Все новости уже на русском, перевод не нужен")
        return [{
            'title_ru':   item.get('title', ''),
            'summary_ru': item.get('summary', ''),
        } for item in items]

    logger.info("Переводим %d новостей из %d...", len(to_translate), len(items))

    # Формируем JSON-массив для GPT
    batch = []
    for i in to_translate:
        item = items[i]
        batch.append({
            'id':      i,
            'title':   (item.get('title') or '')[:200],
            'summary': (item.get('summary') or '')[:400],
        })

    prompt = (
        "Переведи заголовки и описания AI-новостей на русский язык. "
        "Переводи точно и живо, сохраняй технические термины (GPT, LLM, RAG и т.д.). "
        "Верни ТОЛЬКО валидный JSON-массив в том же порядке, без пояснений.\n\n"
        "Формат каждого объекта:\n"
        '{"id": <число>, "title_ru": "...", "summary_ru": "..."}\n\n'
        "Входные данные:\n" + json.dumps(batch, ensure_ascii=False)
    )

    try:
        raw = chat_complete(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4000,
        )
        # Вырезаем JSON из ответа (GPT иногда добавляет ```json ... ```)
        raw = raw.strip()
        if raw.startswith('```'):
            raw = raw.split('```')[1]
            if raw.startswith('json'):
                raw = raw[4:]
        translated = json.loads(raw.strip())
        # Строим словарь id → перевод
        trans_map = {t['id']: t for t in translated}
    except Exception as e:
        logger.warning("Ошибка перевода: %s — используем оригиналы", e)
        trans_map = {}

    # Собираем результат
    result = []
    for i, item in enumerate(items):
        if i in trans_map:
            t = trans_map[i]
            result.append({
                'title_ru':   t.get('title_ru') or item.get('title', ''),
                'summary_ru': t.get('summary_ru') or item.get('summary', ''),
            })
        else:
            # Уже на русском или не попало в перевод
            result.append({
                'title_ru':   item.get('title', ''),
                'summary_ru': item.get('summary', ''),
            })
    return result


def format_item(item, translation: dict) -> dict:
    """Нормализуем поля новости для API, добавляем перевод."""
    published = item.get('published', '') or ''
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(published)
        published = dt.isoformat()
    except Exception:
        pass

    title_orig   = (item.get('title') or '').strip()
    summary_orig = (item.get('summary') or '')[:400].strip()
    title_ru     = (translation.get('title_ru') or title_orig).strip()
    summary_ru   = (translation.get('summary_ru') or summary_orig).strip()

    return {
        'title':       title_ru,           # основной заголовок — русский
        'title_orig':  title_orig,         # оригинал для справки
        'summary':     summary_ru,         # основное описание — русское
        'summary_orig': summary_orig,      # оригинал для справки
        'link':        item.get('link', '') or '',
        'source':      (item.get('source') or '').strip(),
        'published':   published,
        'translated':  title_ru != title_orig,  # флаг: был ли переведён
    }


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/news')
def api_news():
    try:
        force = request.args.get('refresh') == '1'
        news  = get_cached_news(force_refresh=force)
        items_raw = news[:50]

        # Переводим пакетно
        translations = translate_batch(items_raw)
        items = [format_item(n, translations[i]) for i, n in enumerate(items_raw)]

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
    return jsonify({
        'ok':           True,
        'service':      'ai-news-webapp',
        'cached_count': len(_news_cache.get('data') or []),
        'updated':      _news_cache.get('updated'),
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
