#!/usr/bin/env python3
"""
LinkedIn client — OAuth 2.0 + публикация постов через REST API.
Токены хранятся в /opt/ai-news-agent/data/linkedin_tokens.json
"""
import os
import json
import logging
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

TOKENS_FILE = '/opt/ai-news-agent/data/linkedin_tokens.json'
TOKEN_URL   = 'https://www.linkedin.com/oauth/v2/accessToken'
USERINFO_URL = 'https://api.linkedin.com/v2/userinfo'
POSTS_URL    = 'https://api.linkedin.com/v2/ugcPosts'

CLIENT_ID     = os.getenv('LINKEDIN_CLIENT_ID', '')
CLIENT_SECRET = os.getenv('LINKEDIN_CLIENT_SECRET', '')
REDIRECT_URI  = os.getenv('LINKEDIN_REDIRECT_URI', 'https://ai.colliecore.com/linkedin/callback')


# ── Token storage ──────────────────────────────────────────────────────────────

def _load_tokens() -> dict:
    try:
        if os.path.exists(TOKENS_FILE):
            with open(TOKENS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning('_load_tokens error: %s', e)
    return {}


def _save_tokens(data: dict):
    try:
        os.makedirs(os.path.dirname(TOKENS_FILE), exist_ok=True)
        with open(TOKENS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error('_save_tokens error: %s', e)


def get_tokens() -> dict:
    return _load_tokens()


def is_connected() -> bool:
    t = _load_tokens()
    return bool(t.get('access_token'))


# ── OAuth helpers ──────────────────────────────────────────────────────────────

def get_auth_url(state: str = 'linkedin_oauth') -> str:
    """Формирует URL для редиректа на страницу авторизации LinkedIn."""
    params = {
        'response_type': 'code',
        'client_id':     CLIENT_ID,
        'redirect_uri':  REDIRECT_URI,
        'state':         state,
        'scope':         'openid profile email w_member_social',
    }
    from urllib.parse import urlencode
    return 'https://www.linkedin.com/oauth/v2/authorization?' + urlencode(params)


def exchange_code(code: str) -> dict:
    """Обменивает authorization code на access_token + refresh_token."""
    resp = requests.post(TOKEN_URL, data={
        'grant_type':    'authorization_code',
        'code':          code,
        'redirect_uri':  REDIRECT_URI,
        'client_id':     CLIENT_ID,
        'client_secret': CLIENT_SECRET,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if 'access_token' not in data:
        raise RuntimeError(f'LinkedIn token error: {data}')

    # Добавляем время истечения
    expires_in = data.get('expires_in', 5183944)  # ~60 дней по умолчанию
    data['expires_at'] = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).isoformat()

    # Получаем sub (person URN) через userinfo
    try:
        ui = requests.get(USERINFO_URL, headers={
            'Authorization': f'Bearer {data["access_token"]}'
        }, timeout=10).json()
        data['sub']   = ui.get('sub', '')
        data['name']  = ui.get('name', '')
        data['email'] = ui.get('email', '')
    except Exception as e:
        logger.warning('userinfo fetch error: %s', e)

    _save_tokens(data)
    logger.info('LinkedIn tokens saved for %s', data.get('name', 'unknown'))
    return data


def refresh_access_token() -> dict:
    """Обновляет access_token через refresh_token (если есть)."""
    t = _load_tokens()
    if not t.get('refresh_token'):
        raise RuntimeError('No refresh_token available — re-authorize')
    resp = requests.post(TOKEN_URL, data={
        'grant_type':    'refresh_token',
        'refresh_token': t['refresh_token'],
        'client_id':     CLIENT_ID,
        'client_secret': CLIENT_SECRET,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if 'access_token' not in data:
        raise RuntimeError(f'LinkedIn refresh error: {data}')
    expires_in = data.get('expires_in', 5183944)
    data['expires_at'] = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).isoformat()
    # Сохраняем старый refresh_token если новый не пришёл
    if not data.get('refresh_token'):
        data['refresh_token'] = t['refresh_token']
    data['sub']   = t.get('sub', '')
    data['name']  = t.get('name', '')
    data['email'] = t.get('email', '')
    _save_tokens(data)
    return data


def _get_valid_token() -> str:
    """Возвращает действующий access_token, при необходимости обновляет."""
    t = _load_tokens()
    if not t.get('access_token'):
        raise RuntimeError('LinkedIn not connected — please authorize first')

    # Проверяем срок действия (за 5 минут до истечения)
    expires_at = t.get('expires_at')
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= exp - timedelta(minutes=5):
                logger.info('LinkedIn token expired, refreshing...')
                t = refresh_access_token()
        except Exception as e:
            logger.warning('Token expiry check error: %s', e)

    return t['access_token']


# ── Publishing ─────────────────────────────────────────────────────────────────

def publish_post(text: str) -> dict:
    """
    Публикует текстовый пост в LinkedIn от имени авторизованного пользователя.
    Возвращает dict с ключом 'post_id' при успехе.
    """
    token = _get_valid_token()
    t = _load_tokens()
    sub = t.get('sub', '')
    if not sub:
        raise RuntimeError('LinkedIn person URN (sub) not found — re-authorize')

    author = f'urn:li:person:{sub}'

    payload = {
        'author': author,
        'lifecycleState': 'PUBLISHED',
        'specificContent': {
            'com.linkedin.ugc.ShareContent': {
                'shareCommentary': {
                    'text': text
                },
                'shareMediaCategory': 'NONE'
            }
        },
        'visibility': {
            'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC'
        }
    }

    resp = requests.post(
        POSTS_URL,
        headers={
            'Authorization':  f'Bearer {token}',
            'Content-Type':   'application/json',
            'X-Restli-Protocol-Version': '2.0.0',
        },
        json=payload,
        timeout=20,
    )

    if resp.status_code == 201:
        post_id = resp.headers.get('X-RestLi-Id', resp.headers.get('x-restli-id', ''))
        logger.info('Published to LinkedIn: %s', post_id)
        return {'ok': True, 'post_id': post_id}
    else:
        logger.error('LinkedIn publish error %s: %s', resp.status_code, resp.text)
        raise RuntimeError(f'LinkedIn API {resp.status_code}: {resp.text}')


def disconnect():
    """Удаляет сохранённые токены."""
    _save_tokens({})
    logger.info('LinkedIn tokens cleared')
