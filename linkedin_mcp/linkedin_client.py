"""
LinkedIn MCP Client
====================
Thin wrapper that calls LinkedIn MCP server tools directly (in-process).
Used by Flask app to interact with LinkedIn without subprocess overhead.

Usage:
    from mcp.linkedin_client import LinkedInClient
    client = LinkedInClient()
    status = client.get_status()
    url_info = client.get_auth_url()
    result = client.publish_post("Hello LinkedIn!")
"""

import os
import sys
import logging

logger = logging.getLogger('linkedin_client')

# Ensure mcp directory is in path
_MCP_DIR = os.path.dirname(os.path.abspath(__file__))
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)


class LinkedInClient:
    """
    In-process client for LinkedIn MCP server tools.
    Calls tool functions directly without subprocess overhead.
    """

    def __init__(self):
        # Import tool functions from MCP server
        from linkedin_mcp_server import (
            linkedin_get_auth_url,
            linkedin_exchange_code,
            linkedin_get_profile,
            linkedin_publish_post,
            linkedin_get_status,
            linkedin_refresh_token,
            linkedin_disconnect,
        )
        self._get_auth_url    = linkedin_get_auth_url
        self._exchange_code   = linkedin_exchange_code
        self._get_profile     = linkedin_get_profile
        self._publish_post    = linkedin_publish_post
        self._get_status      = linkedin_get_status
        self._refresh_token   = linkedin_refresh_token
        self._disconnect      = linkedin_disconnect

    def get_auth_url(self, state: str = None) -> dict:
        """Generate OAuth authorization URL."""
        return self._get_auth_url(state=state)

    def exchange_code(self, code: str) -> dict:
        """Exchange authorization code for tokens."""
        return self._exchange_code(code=code)

    def get_profile(self) -> dict:
        """Get authenticated user's profile."""
        return self._get_profile()

    def publish_post(self, text: str) -> dict:
        """Publish a text post to LinkedIn."""
        return self._publish_post(text=text)

    def get_status(self) -> dict:
        """Check connection status."""
        return self._get_status()

    def refresh_token(self) -> dict:
        """Refresh access token."""
        return self._refresh_token()

    def disconnect(self) -> dict:
        """Clear stored tokens."""
        return self._disconnect()
