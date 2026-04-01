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
  GET|POST /api/generate?type=post|digest  — генерация поста или дайджеста
                                             POST body: {selected_ids: [int, ...]}
  POST /api/publish         — публикация в Telegram канал
  POST /api/schedule        — отложенная публикация {text, time: "HH:MM"}
  GET  /health              — статус (без авторизации)
"""
import os, sys, json, logging, requests, hashlib, secrets
from datetime import datetime, timezone
from functools import wraps
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

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
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))

def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'Unauthorized'}), 401
            return redirect(url_for('login_page', next=request.path))
        return f(*args, **kwargs)
    return decorated

BOT_TOKEN  = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHANNEL_ID = os.getenv('CHANNEL_ID', '@ai_is_you')

# ── LinkedIn config ────────────────────────────────────────────────────────────
LINKEDIN_CLIENT_ID     = os.getenv('LINKEDIN_CLIENT_ID', '')
LINKEDIN_CLIENT_SECRET = os.getenv('LINKEDIN_CLIENT_SECRET', '')
LINKEDIN_REDIRECT_URI  = os.getenv('LINKEDIN_REDIRECT_URI', 'https://ai.colliecore.com/linkedin/callback')
LINKEDIN_TOKENS_FILE   = '/opt/ai-news-agent/data/linkedin_tokens.json'

# Путь к файлу очереди отложенных публикаций (shared-data volume)
SCHEDULE_FILE  = '/opt/ai-news-agent/data/scheduled_posts.json'
# Персистентный кэш новостей с переводом
NEWS_CACHE_FILE = '/opt/ai-news-agent/data/news_cache.json'
NEWS_CACHE_TTL  = 1800  # 30 минут — после этого фетчим свежие новости автоматически

# ── LinkedIn helpers ───────────────────────────────────────────────────────────

def _li_load_tokens() -> dict:
    try:
        if os.path.exists(LINKEDIN_TOKENS_FILE):
            with open(LINKEDIN_TOKENS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning('_li_load_tokens error: %s', e)
    return {}

def _li_save_tokens(data: dict):
    try:
        os.makedirs(os.path.dirname(LINKEDIN_TOKENS_FILE), exist_ok=True)
        with open(LINKEDIN_TOKENS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error('_li_save_tokens error: %s', e)

def _li_is_connected() -> bool:
    return bool(_li_load_tokens().get('access_token'))

def _li_get_valid_token() -> str:
    from datetime import timedelta
    t = _li_load_tokens()
    if not t.get('access_token'):
        raise RuntimeError('LinkedIn not connected')
    expires_at = t.get('expires_at')
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= exp - timedelta(minutes=5):
                logger.info('LinkedIn token expired, refreshing...')
                t = _li_refresh_token(t)
        except Exception as e:
            logger.warning('Token expiry check: %s', e)
    return t['access_token']

def _li_refresh_token(t: dict) -> dict:
    from datetime import timedelta
    resp = requests.post('https://www.linkedin.com/oauth/v2/accessToken', data={
        'grant_type':    'refresh_token',
        'refresh_token': t['refresh_token'],
        'client_id':     LINKEDIN_CLIENT_ID,
        'client_secret': LINKEDIN_CLIENT_SECRET,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if 'access_token' not in data:
        raise RuntimeError(f'LinkedIn refresh error: {data}')
    data['expires_at'] = (datetime.now(timezone.utc) + timedelta(seconds=data.get('expires_in', 5183944))).isoformat()
    if not data.get('refresh_token'):
        data['refresh_token'] = t.get('refresh_token', '')
    data['sub']   = t.get('sub', '')
    data['name']  = t.get('name', '')
    data['email'] = t.get('email', '')
    _li_save_tokens(data)
    return data

def _li_publish(text: str) -> dict:
    token = _li_get_valid_token()
    t = _li_load_tokens()
    sub = t.get('sub', '')
    if not sub:
        raise RuntimeError('LinkedIn person URN not found — re-authorize')
    payload = {
        'author': f'urn:li:person:{sub}',
        'lifecycleState': 'PUBLISHED',
        'specificContent': {
            'com.linkedin.ugc.ShareContent': {
                'shareCommentary': {'text': text},
                'shareMediaCategory': 'NONE'
            }
        },
        'visibility': {'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC'}
    }
    resp = requests.post(
        'https://api.linkedin.com/v2/ugcPosts',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'X-Restli-Protocol-Version': '2.0.0',
        },
        json=payload, timeout=20,
    )
    if resp.status_code == 201:
        post_id = resp.headers.get('X-RestLi-Id', resp.headers.get('x-restli-id', ''))
        logger.info('Published to LinkedIn: %s', post_id)
        return {'ok': True, 'post_id': post_id}
    raise RuntimeError(f'LinkedIn API {resp.status_code}: {resp.text}')

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

# ── In-memory кэш сырых новостей (fetch) ────────────────────────────────────
_cache = {'data': [], 'updated': None}

def get_news(force=False):
    now = datetime.now().timestamp()
    if force or not _cache['data'] or (now - (_cache['updated'] or 0)) > NEWS_CACHE_TTL:
        news = agent.fetch_news(hours_back=24) or agent.fetch_news(hours_back=72) or []
        _cache['data'] = news
        _cache['updated'] = now
    return _cache['data']

# ── Персистентный кэш переведённых новостей ─────────────────────────────────

def _nc_load() -> dict:
    """Загрузить персистентный кэш из файла."""
    try:
        if os.path.exists(NEWS_CACHE_FILE):
            with open(NEWS_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning('_nc_load error: %s', e)
    return {'items': [], 'updated': 0}

def _nc_save(data: dict):
    """Сохранить персистентный кэш в файл."""
    try:
        os.makedirs(os.path.dirname(NEWS_CACHE_FILE), exist_ok=True)
        with open(NEWS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error('_nc_save error: %s', e)

def _nc_is_fresh() -> bool:
    """Проверить, свеж ли персистентный кэш."""
    nc = _nc_load()
    updated = nc.get('updated', 0)
    return bool(nc.get('items')) and (datetime.now().timestamp() - updated) < NEWS_CACHE_TTL

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

# ── Schedule queue helpers ─────────────────────────────────────────────────────
def _load_schedule():
    try:
        if os.path.exists(SCHEDULE_FILE):
            with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning('_load_schedule error: %s', e)
    return []

def _save_schedule(tasks):
    try:
        os.makedirs(os.path.dirname(SCHEDULE_FILE), exist_ok=True)
        with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error('_save_schedule error: %s', e)

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
    force_refresh = request.args.get('refresh') == '1'
    try:
        # Если не force и кэш свеж — отдаём из файла без LLM-запроса
        if not force_refresh and _nc_is_fresh():
            nc = _nc_load()
            return jsonify({
                'ok':      True,
                'count':   len(nc['items']),
                'updated': nc.get('updated'),
                'cached':  True,
                'items':   nc['items'],
            })

        # Фетчим свежие новости + переводим
        news  = get_news(force=force_refresh)
        items = news[:50]
        trs   = translate_batch(items)
        fmt   = [fmt_item(n, trs[i]) for i, n in enumerate(items)]

        # Сохраняем в персистентный кэш
        now_ts = datetime.now().timestamp()
        _nc_save({'items': fmt, 'updated': now_ts})

        return jsonify({
            'ok':      True,
            'count':   len(fmt),
            'updated': now_ts,
            'cached':  False,
            'items':   fmt,
        })
    except Exception as e:
        logger.error('api_news: %s', e, exc_info=True)
        # Если ошибка при обновлении — отдаём старый кэш если есть
        nc = _nc_load()
        if nc.get('items'):
            return jsonify({
                'ok':      True,
                'count':   len(nc['items']),
                'updated': nc.get('updated'),
                'cached':  True,
                'stale':   True,
                'items':   nc['items'],
            })
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

@app.route('/api/generate', methods=['GET', 'POST'])
@login_required
def api_generate():
    gen_type = request.args.get('type', 'post')
    try:
        news = get_news()
        if not news:
            return jsonify({'ok': False, 'error': 'No news'}), 404

        # Если POST с selected_ids — берём только выбранные новости
        selected_ids = None
        if request.method == 'POST':
            body = request.get_json(force=True) or {}
            selected_ids = body.get('selected_ids')

        if selected_ids is not None and len(selected_ids) > 0:
            # Фильтруем по индексам из allNews (клиент передаёт индексы в массиве news)
            # Переводим весь список, потом выбираем нужные
            all_items = news[:50]
            all_trs   = translate_batch(all_items)
            all_fmt   = [fmt_item(all_items[i], all_trs[i]) for i in range(len(all_items))]
            # selected_ids — индексы в отображаемом списке (до 50)
            valid_ids = [i for i in selected_ids if 0 <= i < len(all_fmt)]
            formatted_items = [all_fmt[i] for i in valid_ids]
            logger.info('api_generate: using %d selected items (ids: %s)', len(formatted_items), valid_ids[:10])
        else:
            # Стандартный режим — все новости
            items = news[:30]
            trs   = translate_batch(items)
            formatted_items = [fmt_item(items[i], trs[i]) for i in range(len(items))]

        # Формируем блок новостей для промпта
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

        # Текущая дата по МСК
        MONTHS_RU = ['января','февраля','марта','апреля','мая','июня',
                     'июля','августа','сентября','октября','ноября','декабря']
        try:
            msk = ZoneInfo('Europe/Moscow')
            now_msk = datetime.now(msk)
        except Exception:
            now_msk = datetime.now()
        today_str = f"{now_msk.day} {MONTHS_RU[now_msk.month-1]} {now_msk.year}"

        if gen_type == 'post':
            prompt = (
                'Ты — редактор Telegram-канала @ai_is_you об искусственном интеллекте.\n'
                'Напиши один яркий пост на русском языке на основе самой интересной новости из списка ниже.\n'
                'Стиль: экспертный, живой, без воды. Длина: 150–250 слов.\n'
                'Без хэштегов. Без эмодзи.\n'
                'ВАЖНО: используй ТОЛЬКО теги <b>текст</b> для жирного и <a href="URL">текст</a> для ссылок.\n'
                'НЕ используй **звёздочки**, *курсив*, markdown-разметку.\n'
                'В конце поста добавь одну строку: <a href="ССЫЛКА">→ источник</a>\n\n'
                'Новости:\n' + news_block
            )
        else:
            n_items = len(formatted_items)
            n_points = min(max(n_items, 3), 12)
            prompt = (
                f'Ты — редактор Telegram-канала @ai_is_you об искусственном интеллекте.\n'
                f'Составь дайджест AI-новостей за {today_str} на русском языке.\n'
                f'\n'
                f'ВАЖНО: используй ТОЛЬКО теги <b>текст</b> для жирного и <a href="URL">текст</a> для ссылок.\n'
                f'НЕ используй **звёздочки**, *курсив*, markdown-разметку, хэштеги, эмодзи.\n'
                f'\n'
                f'Формат дайджеста (строго соблюдай):\n'
                f'\n'
                f'<b>Дайджест AI-новостей — {today_str}</b>\n'
                f'\n'
                f'<b>КРАТКИЙ ЗАГОЛОВОК ПО СУТИ ПЕРВОЙ НОВОСТИ</b>\n'
                f'1–2 предложения с подробным описанием: что произошло, почему важно.\n'
                f'<a href="ССЫЛКА">→ источник</a>\n'
                f'\n'
                f'<b>КРАТКИЙ ЗАГОЛОВОК ПО СУТИ ВТОРОЙ НОВОСТИ</b>\n'
                f'1–2 предложения с подробным описанием.\n'
                f'<a href="ССЫЛКА">→ источник</a>\n'
                f'\n'
                f'... и так далее, {n_points} пунктов.\n'
                f'\n'
                f'Требования к заголовкам:\n'
                f'  - Заголовок = суть новости, а НЕ название источника.\n'
                f'  - Плохо: «TechCrunch: OpenAI выпустила модель».\n'
                f'  - Хорошо: «OpenAI выпустила GPT-5 с поддержкой видео».\n'
                f'\n'
                f'Новости:\n' + news_block
            )

        max_tok = 3000 if gen_type == 'digest' else 800
        content = chat_complete(
            [{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=max_tok,
        )

        # Постобработка: убираем markdown-артефакты которые GPT может добавить вопреки инструкции
        import re
        content = re.sub(r'\*\*(.+?)\*\*', r'\1', content)   # **жирный** → жирный
        content = re.sub(r'\*(.+?)\*',     r'\1', content)   # *курсив* → текст
        content = re.sub(r'#{1,6}\s*',     '',    content)   # ## заголовки
        content = re.sub(r'\n{3,}',        '\n\n', content)  # тройные переносы

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
            'chat_id':                  CHANNEL_ID,
            'text':                     text,
            'parse_mode':               'HTML',
            'disable_web_page_preview': True,
        }, timeout=15)
        data = resp.json()
        if not data.get('ok'):
            raise RuntimeError(data.get('description', 'Telegram API error'))

        logger.info('Published to %s: %s...', CHANNEL_ID, text[:60])
        return jsonify({'ok': True, 'message_id': data['result']['message_id']})
    except Exception as e:
        logger.error('api_publish: %s', e, exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/schedule', methods=['POST'])
@login_required
def api_schedule():
    """Добавить пост в очередь отложенной публикации."""
    try:
        body = request.get_json(force=True)
        text = (body.get('text') or '').strip()
        time_str = (body.get('time') or '').strip()  # "HH:MM"

        if not text:
            return jsonify({'ok': False, 'error': 'Empty text'}), 400
        if not time_str or len(time_str) != 5 or ':' not in time_str:
            return jsonify({'ok': False, 'error': 'Invalid time format, expected HH:MM'}), 400

        # Вычисляем дату публикации по МСК
        try:
            msk = ZoneInfo('Europe/Moscow')
            now_msk = datetime.now(msk)
        except Exception:
            now_msk = datetime.now()

        hour, minute = int(time_str.split(':')[0]), int(time_str.split(':')[1])
        publish_dt = now_msk.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # Если время уже прошло — ставим на завтра
        if publish_dt <= now_msk:
            from datetime import timedelta
            publish_dt += timedelta(days=1)

        task = {
            'id':         secrets.token_hex(8),
            'text':       text,
            'channel_id': CHANNEL_ID,
            'publish_at': publish_dt.isoformat(),
            'created_at': now_msk.isoformat(),
            'status':     'pending',
        }

        tasks = _load_schedule()
        tasks.append(task)
        _save_schedule(tasks)

        logger.info('Scheduled post %s for %s', task['id'], task['publish_at'])
        return jsonify({'ok': True, 'task_id': task['id'], 'publish_at': task['publish_at']})
    except Exception as e:
        logger.error('api_schedule: %s', e, exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

# ── LinkedIn OAuth routes ───────────────────────────────────────────────────────────

@app.route('/linkedin/auth')
@login_required
def linkedin_auth():
    """Redirect to LinkedIn OAuth authorization page."""
    if not LINKEDIN_CLIENT_ID or not LINKEDIN_CLIENT_SECRET:
        return 'LinkedIn credentials not configured', 500
    state = secrets.token_hex(16)
    session['linkedin_state'] = state
    params = {
        'response_type': 'code',
        'client_id':     LINKEDIN_CLIENT_ID,
        'redirect_uri':  LINKEDIN_REDIRECT_URI,
        'state':         state,
        'scope':         'openid profile email w_member_social',
    }
    auth_url = 'https://www.linkedin.com/oauth/v2/authorization?' + urlencode(params)
    return redirect(auth_url)


@app.route('/linkedin/callback')
def linkedin_callback():
    """OAuth callback: exchange code for tokens and save them."""
    error = request.args.get('error')
    if error:
        desc = request.args.get('error_description', error)
        logger.error('LinkedIn OAuth error: %s', desc)
        return f'<h2>LinkedIn ошибка</h2><p>{desc}</p><a href="/">&#8592; Назад</a>', 400

    code  = request.args.get('code', '')
    state = request.args.get('state', '')
    # Проверяем state если сессия ещё жива
    expected_state = session.get('linkedin_state')
    if expected_state and state != expected_state:
        return '<h2>Ошибка state</h2><p>CSRF check failed</p>', 400

    if not code:
        return '<h2>Ошибка</h2><p>No authorization code received</p>', 400

    try:
        from datetime import timedelta
        # Exchange code for tokens
        resp = requests.post('https://www.linkedin.com/oauth/v2/accessToken', data={
            'grant_type':    'authorization_code',
            'code':          code,
            'redirect_uri':  LINKEDIN_REDIRECT_URI,
            'client_id':     LINKEDIN_CLIENT_ID,
            'client_secret': LINKEDIN_CLIENT_SECRET,
        }, timeout=15)
        resp.raise_for_status()
        token_data = resp.json()
        if 'access_token' not in token_data:
            raise RuntimeError(f'Token exchange failed: {token_data}')

        expires_in = token_data.get('expires_in', 5183944)
        token_data['expires_at'] = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()

        # Get user info (sub = person ID) — optional, save token even if this fails
        try:
            ui_resp = requests.get('https://api.linkedin.com/v2/userinfo', headers={
                'Authorization': f'Bearer {token_data["access_token"]}'
            }, timeout=30)
            ui = ui_resp.json() if ui_resp.ok else {}
        except Exception as ui_err:
            logger.warning('userinfo fetch failed (non-fatal): %s', ui_err)
            ui = {}
        token_data['sub']   = ui.get('sub', '')
        token_data['name']  = ui.get('name', '')
        token_data['email'] = ui.get('email', '')

        # If sub is still empty, try /v2/me endpoint as fallback
        if not token_data['sub']:
            try:
                me_resp = requests.get('https://api.linkedin.com/v2/me', headers={
                    'Authorization': f'Bearer {token_data["access_token"]}'
                }, timeout=30)
                me = me_resp.json() if me_resp.ok else {}
                token_data['sub'] = me.get('id', '')
                if not token_data['name']:
                    fn = me.get('localizedFirstName', '')
                    ln = me.get('localizedLastName', '')
                    token_data['name'] = f'{fn} {ln}'.strip()
            except Exception as me_err:
                logger.warning('me fetch failed (non-fatal): %s', me_err)

        _li_save_tokens(token_data)
        session.pop('linkedin_state', None)

        name = token_data.get('name', '') or 'LinkedIn Account'
        logger.info('LinkedIn connected, sub=%s name=%s', token_data.get('sub','?'), name)

        return f'''
<!DOCTYPE html><html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LinkedIn подключён</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:"DM Sans",sans-serif;background:#0A0A0A;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}.card{{background:#141414;border:1px solid #222;border-radius:12px;padding:40px;max-width:400px;width:100%;text-align:center}}.icon{{font-size:48px;margin-bottom:16px}}.title{{font-size:24px;font-weight:700;margin-bottom:8px}}.sub{{color:#888;margin-bottom:24px}}.name{{color:#E8FF47;font-weight:600;font-size:18px;margin-bottom:24px}}.btn{{display:inline-block;background:#E8FF47;color:#0A0A0A;font-weight:700;padding:12px 24px;border-radius:8px;text-decoration:none;font-size:15px}}</style>
</head><body>
<div class="card">
  <div class="icon">✅</div>
  <div class="title">LinkedIn подключён</div>
  <div class="sub">Авторизация успешно завершена</div>
  <div class="name">{name}</div>
  <a href="/" class="btn">← Вернуться в приложение</a>
</div>
</body></html>
'''
    except Exception as e:
        logger.error('linkedin_callback error: %s', e, exc_info=True)
        return f'<h2>Ошибка</h2><p>{e}</p><a href="/">&#8592; Назад</a>', 500


@app.route('/api/linkedin/status')
@login_required
def api_linkedin_status():
    """Return LinkedIn connection status."""
    t = _li_load_tokens()
    connected = bool(t.get('access_token'))
    return jsonify({
        'ok': True,
        'connected': connected,
        'name':  t.get('name', ''),
        'email': t.get('email', ''),
    })


@app.route('/api/linkedin/disconnect', methods=['POST'])
@login_required
def api_linkedin_disconnect():
    """Clear LinkedIn tokens."""
    _li_save_tokens({})
    return jsonify({'ok': True})


@app.route('/api/publish_linkedin', methods=['POST'])
@login_required
def api_publish_linkedin():
    """Publish a post to LinkedIn."""
    try:
        body = request.get_json(force=True)
        text = (body.get('text') or '').strip()
        if not text:
            return jsonify({'ok': False, 'error': 'Empty text'}), 400
        result = _li_publish(text)
        return jsonify(result)
    except Exception as e:
        logger.error('api_publish_linkedin: %s', e, exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/health')
def health():
    """Публичный health-check — без авторизации."""
    scheduled = len([t for t in _load_schedule() if t.get('status') == 'pending'])
    return jsonify({
        'ok':       True,
        'service':  'ai-news-webapp',
        'cached':   len(_cache.get('data') or []),
        'updated':  _cache.get('updated'),
        'scheduled': scheduled,
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
