#!/usr/bin/env python3
"""
notion_sync.py — Генерация идей для контент-плана в Notion на основе RSS трендов.

Логика:
1. Получает список новостей от NewsAgent
2. Через LLM анализирует тренды и генерирует 4-5 идей для постов
3. Добавляет каждую идею в базу «Идеи & Референсы» в Notion
4. Добавляет запись в «Контент-план» со статусом Idea, Topic, Format
"""

import os
import json
import logging
import subprocess
import re
from datetime import datetime, timedelta
from llm_client import chat_complete

logger = logging.getLogger(__name__)

# Notion IDs (из анализа workspace)
# Идеи & Референсы: data_source_id = 788c8967-5122-4c1f-9f8c-a4ecf35d9e50, title field = "Idea"
# Контент-план: database_id = 9bb728cd-e2d6-4690-9c38-4eb1708949b8, title field = "Title"
IDEAS_DS_ID      = "788c8967-5122-4c1f-9f8c-a4ecf35d9e50"   # data_source_id для Идеи & Референсы
CONTENT_PLAN_DB  = "9bb728cd-e2d6-4690-9c38-4eb1708949b8"   # database_id для Контент-план
CONTENT_PLAN_DS  = "9c0f73be-f6f2-4613-bef0-629a0f2d419b"   # data_source_id для Контент-план

# Допустимые значения полей (из схемы Notion)
VALID_TOPICS  = ["AI-стратегия", "Инструменты", "Образование", "Кейсы", "Личное"]
VALID_FORMATS = ["Text", "Carousel", "Poll", "Video", "Link"]


def mcp_call(tool: str, input_data: dict) -> dict:
    """Вызывает Notion MCP инструмент через manus-mcp-cli."""
    try:
        result = subprocess.run(
            ["manus-mcp-cli", "tool", "call", tool, "--server", "notion",
             "--input", json.dumps(input_data, ensure_ascii=False)],
            capture_output=True, text=True, timeout=45
        )
        output = result.stdout + result.stderr
        # Ищем JSON-результат после "Tool execution result:"
        match = re.search(r'Tool execution result:\s*(\{.*\}|\[.*\])', output, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        # Если нет JSON — проверяем на ошибку
        if "Error:" in output:
            logger.error(f"MCP {tool} error: {output[:400]}")
        return {}
    except Exception as e:
        logger.error(f"MCP call failed ({tool}): {e}")
        return {}


def generate_content_ideas(news_items: list) -> list:
    """
    Анализирует новости через LLM и генерирует идеи для постов.
    Возвращает список словарей: title, topic, format, rationale, source_url
    """
    if not news_items:
        return []

    news_text = "\n".join(
        f"- [{item['source']}] {item['title']}" for item in news_items[:20]
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
        # Извлекаем JSON-массив из ответа
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            ideas = json.loads(match.group(0))
            # Валидируем значения
            validated = []
            for idea in ideas:
                topic  = idea.get("topic", "AI-стратегия")
                fmt    = idea.get("format", "Text")
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


def add_idea_to_notion(idea: dict) -> str:
    """
    Добавляет идею в базу «Идеи & Референсы» в Notion.
    Возвращает URL созданной страницы или пустую строку при ошибке.
    """
    title     = idea.get("title", "Без названия")
    rationale = idea.get("rationale", "")
    source_url = idea.get("source_url", "")
    topic     = idea.get("topic", "AI-стратегия")
    fmt       = idea.get("format", "Text")
    today     = datetime.now().strftime('%d.%m.%Y')

    # Формируем контент страницы в Notion Markdown
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

    result = mcp_call("notion-create-pages", {
        "parent": {"data_source_id": IDEAS_DS_ID},
        "pages": [{
            "properties": {
                "Idea": title,
                "Topic Tag": topic,
                "Priority": "High",
                "Source": "AI News Agent"
            },
            "content": content
        }]
    })

    # Обрабатываем разные форматы ответа: {"pages": [{"url": "..."}]}
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
        logger.warning(f"Не удалось получить URL для идеи: '{title}', result={str(result)[:200]}")

    return page_url


def add_to_content_plan(idea: dict, idea_page_url: str = "") -> bool:
    """
    Добавляет запись в «Контент-план» со статусом Idea, Topic, Format.
    """
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

    result = mcp_call("notion-create-pages", {
        "parent": {"database_id": CONTENT_PLAN_DB},
        "pages": [{
            "properties": {
                "Title": title,
                "Status": "Idea",
                "Topic": topic,
                "Format": fmt,
                "date:Publish Date:start": publish_date,
                "date:Publish Date:is_datetime": 0
            },
            "content": content
        }]
    })

    if result:
        logger.info(f"Запись добавлена в Контент-план: '{title}' (Status=Idea, Topic={topic}, Format={fmt})")
        return True

    logger.warning(f"Не удалось добавить в Контент-план: '{title}', result={str(result)[:200]}")
    return False


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

    if not added:
        return "Не удалось добавить идеи в Notion. Проверьте логи."

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
    lines.append('<a href="https://www.notion.so/9bb728cde2d646909c384eb1708949b8">Открыть контент-план →</a>')

    return "\n".join(lines)
