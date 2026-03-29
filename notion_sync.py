#!/usr/bin/env python3
"""
notion_sync.py — Генерация идей для контент-плана в Notion на основе RSS трендов.

Использует mcp_client.NotionMCP для прямых вызовов Notion REST API.
Не зависит от manus-mcp-cli или Node.js.

Логика:
1. Получает список новостей от NewsAgent
2. Через LLM анализирует тренды и генерирует 4-5 идей для постов
3. Добавляет каждую идею в базу «Идеи & Референсы» в Notion
4. Добавляет запись в «Контент-план» со статусом Idea, Topic, Format
"""

import os
import json
import logging
import re
from datetime import datetime, timedelta
from llm_client import chat_complete
from mcp_client import get_mcp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Notion IDs (из анализа workspace через MCP fetch)
# ---------------------------------------------------------------------------
# Идеи & Референсы:
#   database_id    = 76981882-d042-4ec1-8288-00325f74c799  (для parent в create_pages)
#   data_source_id = 788c8967-5122-4c1f-9f8c-a4ecf35d9e50  (для MCP tool_call)
IDEAS_DB_ID     = "76981882-d042-4ec1-8288-00325f74c799"   # database_id (parent)
IDEAS_DS_ID     = "788c8967-5122-4c1f-9f8c-a4ecf35d9e50"   # data_source_id

# Контент-план:
#   database_id    = 9bb728cd-e2d6-4690-9c38-4eb1708949b8  (для parent в create_pages)
#   data_source_id = 9c0f73be-f6f2-4613-bef0-629a0f2d419b  (для MCP tool_call)
CONTENT_PLAN_DB_ID = "9bb728cd-e2d6-4690-9c38-4eb1708949b8"  # database_id (parent)
CONTENT_PLAN_DS_ID = "9c0f73be-f6f2-4613-bef0-629a0f2d419b"  # data_source_id

# Алиас для обратной совместимости
CONTENT_PLAN_DB = CONTENT_PLAN_DB_ID

CONTENT_PLAN_URL = "https://www.notion.so/9bb728cde2d646909c384eb1708949b8"

# Допустимые значения полей (из схемы Notion)
VALID_TOPICS  = ["AI-стратегия", "Инструменты", "Образование", "Кейсы", "Личное"]
VALID_FORMATS = ["Text", "Carousel", "Poll", "Video", "Link"]


# ---------------------------------------------------------------------------
# Генерация идей через LLM
# ---------------------------------------------------------------------------

def generate_content_ideas(news_items: list) -> list:
    """
    Анализирует новости через LLM и генерирует идеи для постов.
    Возвращает список словарей: title, topic, format, rationale, source_url
    """
    if not news_items:
        return []

    news_text = "\n".join(
        f"- [{item.get('source', '')}] {item.get('title', '')} | {item.get('url', item.get('link', ''))}"
        for item in news_items[:20]
    )

    valid_topics_str  = ", ".join(VALID_TOPICS)
    valid_formats_str = ", ".join(VALID_FORMATS)

    system_prompt = f"""Ты — контент-стратег канала @ai_is_you Дмитрия Юдина (Cloud.ru).
Анализируешь новости об ИИ и предлагаешь идеи для постов в авторском стиле Димы.
Допустимые темы: {valid_topics_str}
Допустимые форматы: {valid_formats_str}"""

    prompt = f"""Проанализируй следующие новости об ИИ и предложи 4-5 идей для постов в Telegram-канал @ai_is_you.

Для каждой идеи дай:
- title: короткий заголовок идеи (до 80 символов)
- topic: СТРОГО одно из [{valid_topics_str}]
- format: СТРОГО одно из [{valid_formats_str}]
- rationale: 1-2 предложения — почему эта тема актуальна сейчас и какой угол Димы
- source_url: URL самой релевантной новости-источника (если есть, иначе пустая строка)

Отвечай строго в формате JSON-массива без пояснений:
[
  {{
    "title": "...",
    "topic": "...",
    "format": "...",
    "rationale": "...",
    "source_url": "..."
  }}
]

НОВОСТИ:
{news_text}"""

    try:
        raw = chat_complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=1200
        )
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            ideas = json.loads(match.group(0))
            validated = []
            for idea in ideas:
                topic = idea.get("topic", "AI-стратегия")
                fmt   = idea.get("format", "Text")
                if topic not in VALID_TOPICS:
                    topic = "AI-стратегия"
                if fmt not in VALID_FORMATS:
                    fmt = "Text"
                idea["topic"]  = topic
                idea["format"] = fmt
                validated.append(idea)
            logger.info(f"LLM сгенерировал {len(validated)} идей для контент-плана")
            return validated
        else:
            logger.warning(f"LLM не вернул JSON: {raw[:300]}")
            return []
    except Exception as e:
        logger.error(f"Ошибка генерации идей: {e}")
        return []


# ---------------------------------------------------------------------------
# Добавление в Notion через mcp_client (прямые HTTP запросы)
# ---------------------------------------------------------------------------

def add_idea_to_notion(idea: dict) -> str:
    """
    Добавляет идею в базу «Идеи & Референсы».
    Возвращает URL созданной страницы или пустую строку при ошибке.
    """
    mcp = get_mcp()
    if not mcp.notion:
        raise RuntimeError("NotionMCP недоступен: NOTION_TOKEN не задан")

    title      = idea.get("title", "Без названия")
    rationale  = idea.get("rationale", "")
    source_url = idea.get("source_url", "")
    topic      = idea.get("topic", "AI-стратегия")
    fmt        = idea.get("format", "Text")
    today      = datetime.now().strftime('%d.%m.%Y')

    # Контент страницы в Markdown
    content_parts = [f"## {title}", ""]
    if rationale:
        content_parts.append(rationale)
        content_parts.append("")
    if source_url:
        content_parts.append(f"Источник: {source_url}")
        content_parts.append("")
    content_parts.append(f"Тема: {topic} | Формат: {fmt}")
    content_parts.append(f"Добавлено: {today} (AI агент)")
    content = "\n".join(content_parts)

    try:
        result = mcp.notion.tool_call("notion-create-pages", {
            "parent": {"data_source_id": IDEAS_DS_ID},
            "pages": [{
                "properties": {
                    "Idea": title,
                    "Topic Tag": topic,
                    "Priority": "High",
                    "Source": "AI News Agent",
                },
                "content": content
            }]
        })

        # Извлекаем URL из ответа
        page_url = ""
        if isinstance(result, dict):
            pages = result.get("pages", [])
            if pages and isinstance(pages, list):
                page_url = pages[0].get("url", "")
            else:
                page_url = result.get("url", "")
        elif isinstance(result, list) and len(result) > 0:
            page_url = result[0].get("url", "")

        if page_url:
            logger.info(f"Идея добавлена в Notion Ideas: '{title}' → {page_url}")
        else:
            logger.warning(f"Идея создана, URL не получен: '{title}'")

        return page_url

    except Exception as e:
        logger.error(f"Ошибка добавления идеи в Notion Ideas '{title}': {e}")
        return ""


def add_to_content_plan(idea: dict, idea_page_url: str = "") -> bool:
    """
    Добавляет запись в «Контент-план» со статусом Idea.
    Возвращает True при успехе.
    """
    mcp = get_mcp()
    if not mcp.notion:
        raise RuntimeError("NotionMCP недоступен: NOTION_TOKEN не задан")

    title  = idea.get("title", "Без названия")
    topic  = idea.get("topic", "AI-стратегия")
    fmt    = idea.get("format", "Text")

    # Дата публикации — через 5 дней
    publish_date = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d')

    # Контент страницы
    content_parts = [f"## {title}", ""]
    if idea.get("rationale"):
        content_parts.append(idea["rationale"])
        content_parts.append("")
    if idea_page_url:
        content_parts.append(f"Идея: {idea_page_url}")
    if idea.get("source_url"):
        content_parts.append(f"Источник: {idea['source_url']}")
    content = "\n".join(content_parts)

    try:
        result = mcp.notion.tool_call("notion-create-pages", {
            "parent": {"data_source_id": CONTENT_PLAN_DS_ID},
            "pages": [{
                "properties": {
                    "Title": title,
                    "Status": "Idea",
                    "Topic": topic,
                    "Format": fmt,
                    "date:Publish Date:start": publish_date,
                    "date:Publish Date:is_datetime": 0,
                },
                "content": content
            }]
        })

        if result:
            logger.info(f"Запись добавлена в Контент-план: '{title}' (Status=Idea, Topic={topic}, Format={fmt})")
            return True

        logger.warning(f"Не удалось добавить в Контент-план: '{title}'")
        return False

    except Exception as e:
        logger.error(f"Ошибка добавления в Контент-план '{title}': {e}")
        return False


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------

def sync_ideas_to_notion(news_items: list) -> dict:
    """
    Главная функция: генерирует идеи из новостей и добавляет их в Notion.
    Возвращает словарь: {'ideas': [...], 'added': int, 'errors': int}
    """
    logger.info("=== Запуск синхронизации идей с Notion ===")

    ideas = generate_content_ideas(news_items)
    if not ideas:
        logger.warning("Идеи не сгенерированы")
        return {"ideas": [], "added": 0, "errors": 0}

    added  = 0
    errors = 0
    results = []

    for idea in ideas:
        try:
            # 1. Добавляем в «Идеи & Референсы»
            idea_url = add_idea_to_notion(idea)

            # 2. Добавляем в «Контент-план»
            ok = add_to_content_plan(idea, idea_url)

            if ok or idea_url:
                added += 1
                results.append({
                    "title":      idea.get("title"),
                    "topic":      idea.get("topic"),
                    "format":     idea.get("format"),
                    "notion_url": idea_url
                })
            else:
                errors += 1
        except Exception as e:
            logger.error(f"Ошибка добавления идеи '{idea.get('title')}': {e}")
            errors += 1

    logger.info(f"=== Синхронизация завершена: {added} добавлено, {errors} ошибок ===")
    return {"ideas": results, "added": added, "errors": errors}


def format_notion_report(sync_result: dict) -> str:
    """Форматирует отчёт о добавленных идеях для Telegram."""
    added = sync_result.get("added", 0)
    ideas = sync_result.get("ideas", [])
    errors = sync_result.get("errors", 0)

    if not added:
        err_note = f" ({errors} ошибок)" if errors else ""
        return f"Не удалось добавить идеи в Notion{err_note}. Проверьте логи."

    lines = [
        f"<b>Notion: добавлено {added} идей в контент-план</b>",
        ""
    ]

    for i, idea in enumerate(ideas, 1):
        title = idea.get("title", "—")
        topic = idea.get("topic", "—")
        fmt   = idea.get("format", "—")
        url   = idea.get("notion_url", "")

        line = f"{i}. <b>{title}</b>\n   {topic} · {fmt}"
        if url:
            line += f'  <a href="{url}">Открыть</a>'
        lines.append(line)

    lines.append("")
    lines.append(f'<a href="{CONTENT_PLAN_URL}">Открыть контент-план в Notion →</a>')

    if errors:
        lines.append(f"\n<i>{errors} идей не удалось добавить</i>")

    return "\n".join(lines)
