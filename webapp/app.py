#!/usr/bin/env python3
"""
Mini App backend — Flask API.
Endpoints:
  GET  /api/news          — новости с переводом
  GET  /api/analyze       — GPT-анализ
  GET  /api/generate?type=post|digest  — генерация поста или дайджеста
  POST /api/publish       — публикация в Telegram канал
  GET  /health
"""
import os, sys, json, logging, requests
from datetime import datetime

from flask import Flask, jsonify, send_from_directory, request
from dotenv import load_dotenv

load_dotenv('/opt/ai-news-agent/.env')
sys.path.insert(0, '/opt/ai-news-agent')

from news_agent import NewsAgent
from llm_client import chat_complete

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='')

BOT_TOKEN  = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHANNEL_ID = os.getenv('CHANNEL_ID', '@ai_is_you')

@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return r

agent = NewsAgent(
    openai_api_key=os.getenv('OPENAI_API_KEY', ''),
    telegram_token=BOT_TOKEN,
    user_id=os.getenv('TELEGRAM_USER_ID', ''),
)

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache = {'data': [], 'updated': None}
CACHE_TTL = 1800

def get_news(force=False):
    now = datetime.now().timestamp()
    if force or not _cache['data'] or (now - (_cache['updated'] or 0)) > CACHE_TTL:
        news = agent.fetch_news(hours_back=24) or agent.fetch_news(hours_back=72) or []
        _cache['data'] = news
        _cache['updated'] = now
    return _cache['data']

# ── Translation ────────────────────────────────────────────────────────────────
def _is_ru(t):
    if not t: return True
    return sum(1 for c in t if '\u0400' <= c <= '\u04ff') / max(len(t), 1) > 0.3

def translate_batch(items):
    need = [i for i, n in enumerate(items)
            if not _is_ru(n.get('title','')) or not _is_ru(n.get('summary',''))]
    if not need:
        return [{'title_ru': n.get('title',''), 'summary_ru': n.get('summary','')} for n in items]

    batch = [{'id': i, 'title': (items[i].get('title') or '')[:200],
              'summary': (items[i].get('summary') or '')[:400]} for i in need]
    prompt = (
        'Переведи заголовки и описания AI-новостей на русский. '
        'Сохраняй технические термины (GPT, LLM, RAG и т.д.). '
        'Верни ТОЛЬКО валидный JSON-массив без пояснений.\n'
        'Формат: [{"id":<int>,"title_ru":"...","summary_ru":"..."},...]\n\n'
        + json.dumps(batch, ensure_ascii=False)
    )
    try:
        raw = chat_complete([{"role":"user","content":prompt}], temperature=0.15, max_tokens=4000)
        raw = raw.strip().lstrip('`').lstrip('json').strip('`').strip()
        trans_map = {t['id']: t for t in json.loads(raw)}
    except Exception as e:
        logger.warning('translate_batch error: %s', e)
        trans_map = {}

    result = []
    for i, item in enumerate(items):
        t = trans_map.get(i, {})
        result.append({
            'title_ru':   t.get('title_ru') or item.get('title',''),
            'summary_ru': t.get('summary_ru') or item.get('summary',''),
        })
    return result

def fmt_item(item, tr):
    published = item.get('published','')
    try:
        from email.utils import parsedate_to_datetime
        published = parsedate_to_datetime(published).isoformat()
    except Exception: pass
    t_orig = (item.get('title') or '').strip()
    s_orig = (item.get('summary') or '')[:400].strip()
    t_ru   = (tr.get('title_ru') or t_orig).strip()
    s_ru   = (tr.get('summary_ru') or s_orig).strip()
    return {
        'title':        t_ru,
        'title_orig':   t_orig,
        'summary':      s_ru,
        'summary_orig': s_orig,
        'link':         item.get('link',''),
        'source':       (item.get('source') or '').strip(),
        'published':    published,
        'translated':   t_ru != t_orig,
    }

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/news')
def api_news():
    try:
        news  = get_news(force=request.args.get('refresh')=='1')
        items = news[:50]
        trs   = translate_batch(items)
        return jsonify({
            'ok':      True,
            'count':   len(items),
            'updated': _cache.get('updated'),
            'items':   [fmt_item(n, trs[i]) for i, n in enumerate(items)],
        })
    except Exception as e:
        logger.error('api_news: %s', e, exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/analyze')
def api_analyze():
    try:
        news = get_news()
        if not news:
            return jsonify({'ok': False, 'error': 'No news'}), 404
        analysis = agent.analyze_with_ai(news[:15])
        return jsonify({'ok': True, 'analysis': analysis})
    except Exception as e:
        logger.error('api_analyze: %s', e, exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/generate')
def api_generate():
    """Генерация поста или дайджеста на основе последних новостей."""
    gen_type = request.args.get('type', 'post')  # 'post' или 'digest'
    try:
        news = get_news()
        if not news:
            return jsonify({'ok': False, 'error': 'No news'}), 404

        # Берём переведённые заголовки, описания и ссылки
        items = news[:15]
        trs   = translate_batch(items)
        formatted_items = [fmt_item(n, trs[i]) for i, n in enumerate(items)]

        # Блок новостей с ID и ссылками для промпта
        news_lines = []
        for idx, it in enumerate(formatted_items):
            if not it['title']:
                continue
            link = it.get('link', '')
            source = it.get('source', '')
            news_lines.append(
                f"[{idx}] {it['title']}\n"
                f"    Описание: {it['summary'][:180]}\n"
                f"    Источник: {source}\n"
                f"    Ссылка: {link}"
            )
        news_block = '\n\n'.join(news_lines)

        if gen_type == 'post':
            prompt = (
                'Ты — редактор Telegram-канала @ai_is_you об искусственном интеллекте.\n'
                'Напиши один яркий пост на русском языке на основе самой интересной новости из списка ниже.\n'
                'Стиль: экспертный, живой, без воды. Длина: 150–250 слов.\n'
                'Без хэштегов. Без эмодзи.\n'
                'В конце поста добавь одну строку: <a href="ССЫЛКА">→ источник</a>\n'
                'Используй HTML-тег <a href="..."> для ссылки.\n\n'
                'Новости:\n' + news_block
            )
        else:  # digest
            prompt = (
                'Ты — редактор Telegram-канала @ai_is_you об искусственном интеллекте.\n'
                'Составь еженедельный дайджест на русском языке из новостей ниже.\n'
                'Формат:\n'
                '  - Первая строка: заголовок дайджеста (без тегов)\n'
                '  - Затем 5–7 пунктов. Каждый пункт начинается с:\n'
                '    <a href="ССЫЛКА">→ источник</a>\n'
                '    Затем с новой строки — 1–2 предложения описания новости.\n'
                'Стиль: строгий, информативный, без воды.\n'
                'Без хэштегов. Без эмодзи. Используй HTML-теги <a href="..."> для ссылок.\n\n'
                'Новости:\n' + news_block
            )

        content = chat_complete(
            [{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1200,
        )
        return jsonify({'ok': True, 'content': content, 'type': gen_type})
    except Exception as e:
        logger.error('api_generate: %s', e, exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/publish', methods=['POST'])
def api_publish():
    """Публикация текста в Telegram канал."""
    try:
        body = request.get_json(force=True)
        text = (body.get('text') or '').strip()
        if not text:
            return jsonify({'ok': False, 'error': 'Empty text'}), 400
        if not BOT_TOKEN:
            return jsonify({'ok': False, 'error': 'BOT_TOKEN not set'}), 500

        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
        resp = requests.post(url, json={
            'chat_id':    CHANNEL_ID,
            'text':       text,
            'parse_mode': 'HTML',
        }, timeout=15)
        data = resp.json()
        if not data.get('ok'):
            raise RuntimeError(data.get('description', 'Telegram API error'))

        logger.info('Published to %s: %s...', CHANNEL_ID, text[:60])
        return jsonify({'ok': True, 'message_id': data['result']['message_id']})
    except Exception as e:
        logger.error('api_publish: %s', e, exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/health')
def health():
    return jsonify({
        'ok':      True,
        'service': 'ai-news-webapp',
        'cached':  len(_cache.get('data') or []),
        'updated': _cache.get('updated'),
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
