#!/usr/bin/env python3
"""
LinkedIn MCP Server for YDA News App
=====================================
A Model Context Protocol (MCP) server that exposes LinkedIn API
as structured tools for AI agents.

Tools:
  - linkedin_get_auth_url     : Generate OAuth 2.0 authorization URL
  - linkedin_exchange_code    : Exchange authorization code for tokens
  - linkedin_get_profile      : Get authenticated user's profile
  - linkedin_publish_post     : Publish a text post to LinkedIn
  - linkedin_get_status       : Check connection status and token validity
  - linkedin_refresh_token    : Manually refresh the access token
  - linkedin_disconnect       : Clear stored tokens

Transport: stdio (for integration with Flask app via subprocess)
Also exposes HTTP endpoint at /mcp for SSE transport (optional)

LinkedIn API version: 202503 (March 2025)
Docs: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
"""

import os
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests
from mcp.server.fastmcp import FastMCP

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [linkedin-mcp] %(levelname)s %(message)s',
    stream=sys.stderr,
)
logger = logging.getLogger('linkedin_mcp')

# ── Configuration ─────────────────────────────────────────────────────────────
# Load from environment (set by Flask app or .env)
LINKEDIN_CLIENT_ID     = os.getenv('LINKEDIN_CLIENT_ID', '')
LINKEDIN_CLIENT_SECRET = os.getenv('LINKEDIN_CLIENT_SECRET', '')
LINKEDIN_REDIRECT_URI  = os.getenv('LINKEDIN_REDIRECT_URI', 'https://ai.colliecore.com/linkedin/callback')
TOKENS_FILE            = os.getenv('LINKEDIN_TOKENS_FILE', '/opt/ai-news-agent/data/linkedin_tokens.json')

# LinkedIn REST API version — required in LinkedIn-Version header
LINKEDIN_API_VERSION = '202503'

# OAuth 2.0 scopes:
# - openid, profile, email : OpenID Connect (userinfo endpoint)
# - w_member_social        : Create posts on behalf of member
LINKEDIN_SCOPES = 'openid profile email w_member_social'

# ── MCP Server ────────────────────────────────────────────────────────────────
mcp = FastMCP(
    name='linkedin',
    instructions='LinkedIn API tools: OAuth 2.0 authentication, profile retrieval, and post publishing.',
)

# ── Token storage helpers ─────────────────────────────────────────────────────

def _load_tokens() -> dict:
    """Load tokens from persistent JSON file."""
    try:
        p = Path(TOKENS_FILE)
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        logger.warning('load_tokens: %s', e)
    return {}


def _save_tokens(data: dict) -> None:
    """Save tokens to persistent JSON file."""
    try:
        p = Path(TOKENS_FILE)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        logger.error('save_tokens: %s', e)


def _api_headers(token: str) -> dict:
    """Standard headers for LinkedIn REST API v2 / rest calls."""
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'LinkedIn-Version': LINKEDIN_API_VERSION,
        'X-Restli-Protocol-Version': '2.0.0',
    }


def _get_valid_token() -> str:
    """Return a valid access token, refreshing if needed."""
    t = _load_tokens()
    if not t.get('access_token'):
        raise RuntimeError('LinkedIn not connected. Use linkedin_get_auth_url to start OAuth.')

    expires_at = t.get('expires_at')
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= exp - timedelta(minutes=5):
                logger.info('Token near expiry, refreshing...')
                t = _do_refresh_token(t)
        except Exception as e:
            logger.warning('Expiry check failed: %s', e)

    return t['access_token']


def _do_refresh_token(t: dict) -> dict:
    """Perform token refresh using refresh_token grant."""
    if not t.get('refresh_token'):
        raise RuntimeError('No refresh token available. Please reconnect LinkedIn.')

    resp = requests.post(
        'https://www.linkedin.com/oauth/v2/accessToken',
        data={
            'grant_type':    'refresh_token',
            'refresh_token': t['refresh_token'],
            'client_id':     LINKEDIN_CLIENT_ID,
            'client_secret': LINKEDIN_CLIENT_SECRET,
        },
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f'Token refresh failed ({resp.status_code}): {resp.text}')

    data = resp.json()
    if 'access_token' not in data:
        raise RuntimeError(f'Unexpected refresh response: {data}')

    data['expires_at'] = (
        datetime.now(timezone.utc) + timedelta(seconds=data.get('expires_in', 5183944))
    ).isoformat()

    # Preserve refresh token and profile if not returned
    if not data.get('refresh_token'):
        data['refresh_token'] = t.get('refresh_token', '')
    data['sub']   = t.get('sub', '')
    data['name']  = t.get('name', '')
    data['email'] = t.get('email', '')

    _save_tokens(data)
    logger.info('Token refreshed successfully')
    return data


def _fetch_userinfo(token: str) -> dict:
    """Fetch user profile from /v2/userinfo (OpenID Connect endpoint)."""
    resp = requests.get(
        'https://api.linkedin.com/v2/userinfo',
        headers={'Authorization': f'Bearer {token}'},
        timeout=15,
    )
    if resp.ok:
        return resp.json()
    logger.warning('userinfo %s: %s', resp.status_code, resp.text[:200])
    return {}


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def linkedin_get_auth_url(state: Optional[str] = None) -> dict:
    """
    Generate a LinkedIn OAuth 2.0 authorization URL.

    The user must open this URL in their browser to grant access.
    After authorization, LinkedIn redirects to the callback URL with a 'code' parameter.

    Args:
        state: Optional CSRF protection token (random string). Generated automatically if not provided.

    Returns:
        dict with:
          - auth_url (str): URL to open in browser
          - state (str): State token to verify in callback
    """
    import secrets as _secrets
    if not LINKEDIN_CLIENT_ID:
        return {'error': 'LINKEDIN_CLIENT_ID not configured'}

    _state = state or _secrets.token_hex(16)
    params = {
        'response_type': 'code',
        'client_id':     LINKEDIN_CLIENT_ID,
        'redirect_uri':  LINKEDIN_REDIRECT_URI,
        'state':         _state,
        'scope':         LINKEDIN_SCOPES,
    }
    auth_url = 'https://www.linkedin.com/oauth/v2/authorization?' + urlencode(params)
    logger.info('Generated auth URL (state=%s)', _state)
    return {
        'auth_url': auth_url,
        'state': _state,
        'redirect_uri': LINKEDIN_REDIRECT_URI,
        'scopes': LINKEDIN_SCOPES,
    }


@mcp.tool()
def linkedin_exchange_code(code: str) -> dict:
    """
    Exchange an OAuth 2.0 authorization code for access and refresh tokens.

    Call this after the user completes the LinkedIn authorization flow and you
    receive the 'code' parameter in the callback URL.

    Args:
        code: Authorization code from LinkedIn callback URL

    Returns:
        dict with:
          - ok (bool): Success flag
          - name (str): User's display name
          - email (str): User's email
          - sub (str): LinkedIn person ID (used as URN)
          - expires_at (str): Token expiry datetime (ISO 8601)
          - error (str): Error message if ok=False
    """
    if not LINKEDIN_CLIENT_ID or not LINKEDIN_CLIENT_SECRET:
        return {'ok': False, 'error': 'LinkedIn credentials not configured'}

    try:
        # Exchange code for tokens
        resp = requests.post(
            'https://www.linkedin.com/oauth/v2/accessToken',
            data={
                'grant_type':   'authorization_code',
                'code':         code,
                'redirect_uri': LINKEDIN_REDIRECT_URI,
                'client_id':    LINKEDIN_CLIENT_ID,
                'client_secret': LINKEDIN_CLIENT_SECRET,
            },
            timeout=15,
        )
        if not resp.ok:
            return {'ok': False, 'error': f'Token exchange failed ({resp.status_code}): {resp.text}'}

        token_data = resp.json()
        if 'access_token' not in token_data:
            return {'ok': False, 'error': f'No access_token in response: {token_data}'}

        # Set expiry
        expires_in = token_data.get('expires_in', 5183944)
        token_data['expires_at'] = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()

        # Fetch user profile
        profile = _fetch_userinfo(token_data['access_token'])
        token_data['sub']   = profile.get('sub', '')
        token_data['name']  = profile.get('name', '')
        token_data['email'] = profile.get('email', '')

        _save_tokens(token_data)
        logger.info('OAuth complete: sub=%s name=%s', token_data['sub'], token_data['name'])

        return {
            'ok': True,
            'name':       token_data['name'],
            'email':      token_data['email'],
            'sub':        token_data['sub'],
            'expires_at': token_data['expires_at'],
        }

    except Exception as e:
        logger.error('exchange_code: %s', e)
        return {'ok': False, 'error': str(e)}


@mcp.tool()
def linkedin_get_profile() -> dict:
    """
    Get the authenticated LinkedIn user's profile information.

    Uses the OpenID Connect /v2/userinfo endpoint which is always available
    with the 'openid profile email' scopes.

    Returns:
        dict with:
          - ok (bool): Success flag
          - sub (str): LinkedIn person ID
          - name (str): Full display name
          - given_name (str): First name
          - family_name (str): Last name
          - email (str): Email address
          - picture (str): Profile photo URL
          - error (str): Error message if ok=False
    """
    try:
        token = _get_valid_token()
        profile = _fetch_userinfo(token)
        if not profile:
            return {'ok': False, 'error': 'Could not fetch profile from LinkedIn'}

        # Update stored tokens with latest profile info
        t = _load_tokens()
        t['sub']   = profile.get('sub', t.get('sub', ''))
        t['name']  = profile.get('name', t.get('name', ''))
        t['email'] = profile.get('email', t.get('email', ''))
        _save_tokens(t)

        return {
            'ok':          True,
            'sub':         profile.get('sub', ''),
            'name':        profile.get('name', ''),
            'given_name':  profile.get('given_name', ''),
            'family_name': profile.get('family_name', ''),
            'email':       profile.get('email', ''),
            'picture':     profile.get('picture', ''),
        }
    except Exception as e:
        logger.error('get_profile: %s', e)
        return {'ok': False, 'error': str(e)}


@mcp.tool()
def linkedin_publish_post(text: str) -> dict:
    """
    Publish a text post to LinkedIn on behalf of the authenticated user.

    Uses the LinkedIn REST Posts API (POST /rest/posts) which replaced
    the deprecated /v2/ugcPosts endpoint.

    Args:
        text: Post content (plain text, up to ~3000 chars recommended)

    Returns:
        dict with:
          - ok (bool): Success flag
          - post_id (str): LinkedIn post URN (e.g. urn:li:share:...)
          - error (str): Error message if ok=False
    """
    if not text or not text.strip():
        return {'ok': False, 'error': 'Post text cannot be empty'}

    try:
        token = _get_valid_token()
        t = _load_tokens()
        sub = t.get('sub', '')

        # Ensure we have the person URN
        if not sub:
            profile = _fetch_userinfo(token)
            sub = profile.get('sub', '')
            if sub:
                t['sub']   = sub
                t['name']  = t.get('name') or profile.get('name', '')
                t['email'] = t.get('email') or profile.get('email', '')
                _save_tokens(t)
            else:
                return {'ok': False, 'error': 'LinkedIn person URN not found — please reconnect'}

        # Build payload for REST Posts API
        payload = {
            'author':     f'urn:li:person:{sub}',
            'commentary': text.strip(),
            'visibility': 'PUBLIC',
            'distribution': {
                'feedDistribution': 'MAIN_FEED',
                'targetEntities': [],
                'thirdPartyDistributionChannels': [],
            },
            'lifecycleState': 'PUBLISHED',
            'isReshareDisabledByAuthor': False,
        }

        resp = requests.post(
            'https://api.linkedin.com/rest/posts',
            headers=_api_headers(token),
            json=payload,
            timeout=20,
        )

        if resp.status_code == 201:
            post_id = resp.headers.get('x-restli-id', resp.headers.get('X-RestLi-Id', ''))
            logger.info('Post published: %s', post_id)
            return {'ok': True, 'post_id': post_id}

        # Parse error details
        try:
            err = resp.json()
            msg = err.get('message') or err.get('error') or resp.text
        except Exception:
            msg = resp.text
        logger.error('Publish failed %s: %s', resp.status_code, msg)
        return {'ok': False, 'error': f'LinkedIn API {resp.status_code}: {msg}'}

    except Exception as e:
        logger.error('publish_post: %s', e)
        return {'ok': False, 'error': str(e)}


@mcp.tool()
def linkedin_get_status() -> dict:
    """
    Check LinkedIn connection status and token validity.

    Returns:
        dict with:
          - connected (bool): Whether a valid token exists
          - name (str): Connected user's name (if connected)
          - email (str): Connected user's email (if connected)
          - sub (str): LinkedIn person ID (if connected)
          - expires_at (str): Token expiry datetime (if connected)
          - token_valid (bool): Whether token is still valid (not expired)
    """
    t = _load_tokens()
    if not t.get('access_token'):
        return {'connected': False, 'name': '', 'email': '', 'sub': ''}

    token_valid = True
    expires_at = t.get('expires_at', '')
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            token_valid = datetime.now(timezone.utc) < exp
        except Exception:
            pass

    return {
        'connected':   True,
        'name':        t.get('name', ''),
        'email':       t.get('email', ''),
        'sub':         t.get('sub', ''),
        'expires_at':  expires_at,
        'token_valid': token_valid,
    }


@mcp.tool()
def linkedin_refresh_token() -> dict:
    """
    Manually refresh the LinkedIn access token using the stored refresh token.

    LinkedIn access tokens expire after ~60 days. Refresh tokens last ~1 year.
    Tokens are refreshed automatically when needed, but you can force a refresh here.

    Returns:
        dict with:
          - ok (bool): Success flag
          - expires_at (str): New token expiry datetime
          - error (str): Error message if ok=False
    """
    try:
        t = _load_tokens()
        if not t.get('access_token'):
            return {'ok': False, 'error': 'Not connected to LinkedIn'}
        new_t = _do_refresh_token(t)
        return {'ok': True, 'expires_at': new_t.get('expires_at', '')}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@mcp.tool()
def linkedin_disconnect() -> dict:
    """
    Disconnect LinkedIn by clearing all stored tokens.

    After calling this, the user must re-authorize via linkedin_get_auth_url.

    Returns:
        dict with:
          - ok (bool): Always True
    """
    _save_tokens({})
    logger.info('LinkedIn disconnected — tokens cleared')
    return {'ok': True, 'message': 'LinkedIn disconnected successfully'}


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='LinkedIn MCP Server')
    parser.add_argument('--transport', choices=['stdio', 'sse'], default='stdio',
                        help='Transport mode (default: stdio)')
    parser.add_argument('--port', type=int, default=8765,
                        help='Port for SSE transport (default: 8765)')
    args = parser.parse_args()

    logger.info('Starting LinkedIn MCP Server (transport=%s)', args.transport)

    if args.transport == 'sse':
        mcp.run(transport='sse', port=args.port)
    else:
        mcp.run(transport='stdio')
