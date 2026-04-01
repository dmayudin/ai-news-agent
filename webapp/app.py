#!/usr/bin/env python3
"""
Mini App backend — Flask API.
Endpoints:
  GET  /                    — Mini App (требует авторизации)
  GET  /login               — страница входа
  POST /login               — проверка логина/пароля
  GET  /logout              — выход
  GET  /api/news            — новости с переводом
  GET  /api/analyze         — GPT-анализ
  GET  /api/generate?type=post|digest  — генерация поста или дайджеста
  POST /api/publish         — публикация в Telegram канал
  GET  /health              — статус (без авторизации)
"""
import os, sys, json, logging, requests, hashlib, secrets
from datetime import datetime
from functools import wraps

from flask import (Flask, jsonify, send_from_directory, request,
                   redirect, url_for, session, make_response)
from dotenv import load_dotenv

load_dotenv('/opt/ai-news-agent/.env')
sys.path.insert(0, '/opt/ai-news-agent')

from news_agent import NewsAgent
from llm_client import chat_complete

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='')

# ── Auth config ────────────────────────────────────────────────────────────────
APP_USERNAME = os.getenv('APP_USERNAME', 'dmayudin')
APP_PASSWORD = os.getenv('APP_PASSWORD', 'BulochkaSobachka2026!')
# Секретный ключ для подписи сессий — генерируется один раз при старте
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))

def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            # API-запросы получают 401, браузерные — редирект на /login
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'Unauthorized'}), 401
            return redirect(url_for('login_page', next=request.path))
        return f(*args, **kwargs)
    return decorated

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

# ── Auth routes ────────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET'])
def login_page():
    if session.get('authenticated'):
        return redirect('/')
    return send_from_directory('static', 'login.html')

@app.route('/login', methods=['POST'])
def login_submit():
    data = request.get_json(force=True) if request.is_json else request.form
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()

    if username == APP_USERNAME and _hash(password) == _hash(APP_PASSWORD):
        session.permanent = True
        session['authenticated'] = True
        session['user'] = username
        logger.info('Login OK: %s from %s', username, request.remote_addr)
        if request.is_json:
            return jsonify({'ok': True})
        return redirect(data.get('next') or '/')
    else:
        logger.warning('Login FAIL: %s from %s', username, request.remote_addr)
        if request.is_json:
            return jsonify({'ok': False, 'error': 'Invalid credentials'}), 401
        return redirect(url_for('login_page') + '?error=1')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

# ── Protected routes ───────────────────────────────────────────────────────────
@app.route('/')
@login_required
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/news')
@login_required
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
@login_required
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
@login_required
def api_generate():
    gen_type = request.args.get('type', 'post')
    try:
        news = get_news()
        if not news:
            return jsonify({'ok': False, 'error': 'No news'}), 404

        items = news[:15]
        trs   = translate_batch(items)
        formatted_items = [fmt_item(n, trs[i]) for i, n in enumerate(items)]

        news_lines = []
        for idx, it in enumerate(formatted_items):
            if not it['title']:
                continue
            link   = it.get('link', '')
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
        else:
            # Дата сегодня по МСК — передаётся в промпт, чтобы GPT вставил её в заголовок
            import locale
            today_str = datetime.now().strftime('%-d %B %Y')
            # Переводим месяц вручную (локаль не всегда доступна в Docker)
            _months = {
                'January':'января','February':'февраля','March':'марта',
                'April':'апреля','May':'мая','June':'июня',
                'July':'июля','August':'августа','September':'сентября',
                'October':'октября','November':'ноября','December':'декабря',
            }
            for en, ru_m in _months.items():
                today_str = today_str.replace(en, ru_m)
            prompt = (
                'Ты — редактор Telegram-канала @ai_is_you об искусственном интеллекте.\n'
                f'Составь дайджест AI-новостей за {today_str} на русском языке из новостей ниже.\n'
                '\n'
                'Требуемый формат (копируй точно, без отступов):\n'
                '\n'
                f'<b>Дайджест AI-новостей — {today_str}</b>\n'
                '\n'
                '<b>ЗАГОЛОВОК ПЕРВОЙ НОВОСТИ</b>\n'
                '1–2 предложения: что произошло, почему важно.\n'
                '<a href="URL_ПЕРВОЙ_НОВОСТИ">→ источник</a>\n'
                '\n'
                '<b>ЗАГОЛОВОК ВТОРОЙ НОВОСТИ</b>\n'
                '1–2 предложения: что произошло, почему важно.\n'
                '<a href="URL_ВТОРОЙ_НОВОСТИ">→ источник</a>\n'
                '\n'
                '... и так далее для каждой из 5–7 новостей.\n'
                '\n'
                'Правила:\n'
                '1. Заголовок каждого пункта — суть новости, не название источника.\n'
                '   Плохо: «TechCrunch: OpenAI выпустила модель».\n'
                '   Хорошо: «OpenAI выпустила GPT-5 с поддержкой видео».\n'
                '2. Описание: 1–2 предложения, конкретно, без воды.\n'
                '3. Ссылка сразу после описания: <a href="реальный_URL">→ источник</a>\n'
                '4. Между пунктами обязательно пустая строка.\n'
                '5. Без хэштегов, без эмодзи, без маркдаун звёздочек (**), без тире.\n'
                '6. Только HTML-теги <b> и <a href="..."> — больше никаких HTML-тегов.\n'
                '\n'
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
@login_required
def api_publish():
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
    """Публичный health-check — без авторизации."""
    return jsonify({
        'ok':      True,
        'service': 'ai-news-webapp',
        'cached':  len(_cache.get('data') or []),
        'updated': _cache.get('updated'),
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
