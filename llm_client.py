#!/usr/bin/env python3
"""
LLM Client с автоматическим fallback: OpenAI gpt-4.1 → OpenRouter claude-sonnet-4-5
"""

import logging
import os
from openai import OpenAI

logger = logging.getLogger(__name__)

OPENAI_KEY      = os.getenv('OPENAI_API_KEY', '')
OPENROUTER_KEY  = os.getenv('OPENROUTER_API_KEY', '')

OPENAI_MODEL    = "gpt-4.1"
FALLBACK_MODEL  = "anthropic/claude-sonnet-4-5"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def chat_complete(messages: list, temperature: float = 0.75, max_tokens: int = 800) -> str:
    """
    Выполняет chat completion с автоматическим fallback.
    Сначала пробует OpenAI gpt-4.1, при ошибке — OpenRouter claude-sonnet-4-5.
    Возвращает текст ответа или бросает исключение если оба провайдера недоступны.
    """
    # --- Попытка 1: OpenAI ---
    if OPENAI_KEY:
        try:
            client = OpenAI(api_key=OPENAI_KEY)
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            logger.info(f"LLM: OpenAI {OPENAI_MODEL} — OK")
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"LLM: OpenAI failed ({e}), switching to OpenRouter fallback...")

    # --- Попытка 2: OpenRouter Claude Sonnet ---
    if OPENROUTER_KEY:
        try:
            client = OpenAI(
                api_key=OPENROUTER_KEY,
                base_url=OPENROUTER_BASE,
            )
            resp = client.chat.completions.create(
                model=FALLBACK_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_headers={
                    "HTTP-Referer": "https://github.com/dmayudin/ai-news-agent",
                    "X-Title": "AI News Agent"
                }
            )
            logger.info(f"LLM: OpenRouter {FALLBACK_MODEL} (fallback) — OK")
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"LLM: OpenRouter fallback also failed: {e}")
            raise RuntimeError(f"Оба LLM провайдера недоступны. OpenAI: см. лог. OpenRouter: {e}")

    raise RuntimeError("Не настроены API ключи для LLM (OPENAI_API_KEY или OPENROUTER_API_KEY)")
