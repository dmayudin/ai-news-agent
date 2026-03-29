"""
mcp_client.py — Python MCP-style клиент для AI News Agent.

Архитектура:
    MCPClient (единая точка входа)
        ├── NotionMCP   → Notion REST API v1
        └── (будущий)   → Google Calendar, Slack, GitHub и т.д.

Использование:
    from mcp_client import MCPClient
    mcp = MCPClient()

    # Поиск страниц в Notion
    results = mcp.notion.search("контент-план")

    # Создание страницы в базе данных
    page = mcp.notion.create_page(
        parent_type="database_id",
        parent_id="abc123...",
        properties={"Title": "Моя идея", "Status": "Idea"},
        content="## Описание\nТекст страницы"
    )

    # Получение страницы/базы данных
    data = mcp.notion.fetch("https://www.notion.so/...")

Добавление нового MCP провайдера:
    class MyNewMCP(BaseMCP):
        def __init__(self, token: str): ...
        # реализуй нужные методы

    class MCPClient:
        def __init__(self):
            self.notion = NotionMCP(os.getenv("NOTION_TOKEN"))
            self.my_tool = MyNewMCP(os.getenv("MY_TOOL_TOKEN"))
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Базовый класс
# ---------------------------------------------------------------------------

class BaseMCP:
    """Базовый класс для всех MCP провайдеров."""

    name: str = "base"

    def tool_call(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """Единый интерфейс вызова инструмента (аналог MCP tool_call)."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Notion MCP провайдер
# ---------------------------------------------------------------------------

class NotionMCP(BaseMCP):
    """
    Notion MCP провайдер — обёртка над Notion REST API v1.

    Поддерживаемые инструменты (tool_call):
        notion-search           → поиск по workspace
        notion-fetch            → получение страницы/базы по URL или ID
        notion-create-pages     → создание страниц в базе данных
        notion-update-page      → обновление свойств страницы
        notion-query-database   → запрос к базе данных с фильтрами

    Прямые методы (удобные обёртки):
        search(query)
        fetch(url_or_id)
        create_page(parent_type, parent_id, properties, content)
        update_page(page_id, properties)
        query_database(database_id, filter_dict, sorts)
    """

    API_BASE = "https://api.notion.com/v1"
    NOTION_VERSION = "2022-06-28"

    def __init__(self, token: str):
        if not token:
            raise ValueError("NOTION_TOKEN не задан")
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": self.NOTION_VERSION,
            "Content-Type": "application/json",
        }
        self.name = "notion"

    # ------------------------------------------------------------------
    # Единый интерфейс tool_call (MCP-совместимый)
    # ------------------------------------------------------------------

    def tool_call(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """
        Вызов инструмента по имени — аналог MCP protocol tool_call.

        Поддерживаемые tool_name:
            notion-search, notion-fetch, notion-create-pages,
            notion-update-page, notion-query-database
        """
        dispatch = {
            "notion-search":         self._tool_search,
            "notion-fetch":          self._tool_fetch,
            "notion-create-pages":   self._tool_create_pages,
            "notion-update-page":    self._tool_update_page,
            "notion-query-database": self._tool_query_database,
        }
        handler = dispatch.get(tool_name)
        if not handler:
            raise ValueError(f"NotionMCP: неизвестный инструмент '{tool_name}'")
        logger.debug(f"NotionMCP.tool_call: {tool_name} params={list(params.keys())}")
        return handler(params)

    # ------------------------------------------------------------------
    # Инструменты (tool handlers)
    # ------------------------------------------------------------------

    def _tool_search(self, params: Dict) -> Dict:
        query = params.get("query", "")
        return self.search(query)

    def _tool_fetch(self, params: Dict) -> Dict:
        url_or_id = params.get("url") or params.get("id") or params.get("page_id") or ""
        return self.fetch(url_or_id)

    def _tool_create_pages(self, params: Dict) -> Dict:
        """
        Параметры:
            parent: {"database_id": "..."} или {"data_source_id": "..."}
            pages: [{"properties": {...}, "content": "..."}]
        """
        parent = params.get("parent", {})
        pages_data = params.get("pages", [])

        # Определяем parent_type и parent_id
        if "database_id" in parent:
            parent_type = "database_id"
            parent_id = parent["database_id"]
        elif "data_source_id" in parent:
            parent_type = "database_id"
            parent_id = parent["data_source_id"]
        elif "page_id" in parent:
            parent_type = "page_id"
            parent_id = parent["page_id"]
        else:
            raise ValueError(f"NotionMCP: неизвестный тип parent: {parent}")

        created_pages = []
        for page_data in pages_data:
            props_raw = page_data.get("properties", {})
            content = page_data.get("content", "")
            page = self.create_page(
                parent_type=parent_type,
                parent_id=parent_id,
                properties=props_raw,
                content=content,
            )
            created_pages.append(page)

        return {"pages": created_pages}

    def _tool_update_page(self, params: Dict) -> Dict:
        page_id = params.get("page_id", "")
        properties = params.get("properties", {})
        return self.update_page(page_id, properties)

    def _tool_query_database(self, params: Dict) -> Dict:
        database_id = params.get("database_id", "")
        filter_dict = params.get("filter")
        sorts = params.get("sorts")
        return self.query_database(database_id, filter_dict, sorts)

    # ------------------------------------------------------------------
    # Прямые методы API
    # ------------------------------------------------------------------

    def search(self, query: str = "", object_type: str = None) -> Dict:
        """Поиск по Notion workspace."""
        body: Dict[str, Any] = {}
        if query:
            body["query"] = query
        if object_type:
            body["filter"] = {"value": object_type, "property": "object"}
        return self._post("/search", body)

    def fetch(self, url_or_id: str) -> Dict:
        """Получить страницу или базу данных по URL или ID."""
        obj_id = self._extract_id(url_or_id)
        # Пробуем как базу данных, потом как страницу
        try:
            result = self._get(f"/databases/{obj_id}")
            if "object" in result:
                return result
        except Exception:
            pass
        return self._get(f"/pages/{obj_id}")

    def create_page(
        self,
        parent_type: str,
        parent_id: str,
        properties: Dict[str, Any],
        content: str = "",
    ) -> Dict:
        """
        Создать страницу в базе данных или как дочернюю страницу.

        parent_type: "database_id" | "page_id"
        properties: словарь свойств в упрощённом формате (строки, даты)
        content: текст в Markdown (будет преобразован в Notion blocks)
        """
        # Получаем схему базы данных для правильного форматирования свойств
        db_schema = {}
        if parent_type == "database_id":
            try:
                db_info = self._get(f"/databases/{parent_id}")
                db_schema = db_info.get("properties", {})
            except Exception as e:
                logger.warning(f"Не удалось получить схему БД {parent_id}: {e}")

        notion_props = self._build_properties(properties, db_schema)
        children = self._markdown_to_blocks(content) if content else []

        body = {
            "parent": {parent_type: parent_id},
            "properties": notion_props,
        }
        if children:
            body["children"] = children

        result = self._post("/pages", body)
        logger.info(f"Страница создана: {result.get('url', result.get('id', '?'))}")
        return result

    def update_page(self, page_id: str, properties: Dict[str, Any]) -> Dict:
        """Обновить свойства существующей страницы."""
        # Получаем схему через родительскую базу
        db_schema = {}
        try:
            page = self._get(f"/pages/{page_id}")
            parent = page.get("parent", {})
            if "database_id" in parent:
                db_info = self._get(f"/databases/{parent['database_id']}")
                db_schema = db_info.get("properties", {})
        except Exception as e:
            logger.warning(f"Не удалось получить схему для обновления: {e}")

        notion_props = self._build_properties(properties, db_schema)
        return self._patch(f"/pages/{page_id}", {"properties": notion_props})

    def query_database(
        self,
        database_id: str,
        filter_dict: Optional[Dict] = None,
        sorts: Optional[List] = None,
        page_size: int = 50,
    ) -> Dict:
        """Запрос к базе данных Notion с фильтрами и сортировкой."""
        body: Dict[str, Any] = {"page_size": page_size}
        if filter_dict:
            body["filter"] = filter_dict
        if sorts:
            body["sorts"] = sorts
        return self._post(f"/databases/{database_id}/query", body)

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    def _build_properties(
        self, props: Dict[str, Any], schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Преобразует упрощённый словарь свойств в формат Notion API.
        Автоматически определяет тип поля из схемы базы данных.
        """
        notion_props: Dict[str, Any] = {}

        for key, value in props.items():
            # Пропускаем специальные ключи с префиксами (устаревший формат)
            if key.startswith("date:"):
                continue

            prop_schema = schema.get(key, {})
            prop_type = prop_schema.get("type", "")

            # Автоопределение типа если схема не получена
            if not prop_type:
                prop_type = self._guess_prop_type(key, value)

            notion_props[key] = self._format_property(key, value, prop_type)

        return notion_props

    def _guess_prop_type(self, key: str, value: Any) -> str:
        """Угадывает тип свойства по имени ключа и значению."""
        key_lower = key.lower()
        if key_lower in ("title", "name", "idea", "заголовок"):
            return "title"
        if key_lower in ("status", "статус"):
            return "status"
        if key_lower in ("date", "publish date", "дата", "publish_date"):
            return "date"
        if isinstance(value, bool):
            return "checkbox"
        if isinstance(value, (int, float)):
            return "number"
        # По умолчанию — rich_text (для select/multi_select нужна схема)
        return "rich_text"

    def _format_property(self, key: str, value: Any, prop_type: str) -> Dict:
        """Форматирует одно свойство в формат Notion API."""
        if prop_type == "title":
            return {"title": [{"text": {"content": str(value)}}]}

        if prop_type in ("rich_text", "text"):
            return {"rich_text": [{"text": {"content": str(value)}}]}

        if prop_type == "select":
            return {"select": {"name": str(value)}}

        if prop_type == "multi_select":
            if isinstance(value, list):
                return {"multi_select": [{"name": v} for v in value]}
            return {"multi_select": [{"name": str(value)}]}

        if prop_type == "status":
            return {"status": {"name": str(value)}}

        if prop_type == "date":
            if isinstance(value, str):
                return {"date": {"start": value}}
            if isinstance(value, datetime):
                return {"date": {"start": value.strftime("%Y-%m-%d")}}
            return {"date": {"start": str(value)}}

        if prop_type == "checkbox":
            return {"checkbox": bool(value)}

        if prop_type == "number":
            return {"number": value}

        if prop_type == "url":
            return {"url": str(value)}

        if prop_type == "email":
            return {"email": str(value)}

        # Fallback — rich_text
        return {"rich_text": [{"text": {"content": str(value)}}]}

    def _markdown_to_blocks(self, text: str) -> List[Dict]:
        """
        Конвертирует простой Markdown в Notion blocks.
        Поддерживает: заголовки ##/###, параграфы, маркированные списки.
        """
        blocks = []
        for line in text.split("\n"):
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("## "):
                blocks.append({
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": [{"text": {"content": line[3:]}}]},
                })
            elif line.startswith("### "):
                blocks.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": [{"text": {"content": line[4:]}}]},
                })
            elif line.startswith("- ") or line.startswith("* "):
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"text": {"content": line[2:]}}]
                    },
                })
            else:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": line}}]},
                })
        return blocks

    @staticmethod
    def _extract_id(url_or_id: str) -> str:
        """Извлекает UUID из URL Notion или возвращает ID как есть."""
        # UUID без дефисов в конце URL
        match = re.search(
            r"([0-9a-f]{8}[0-9a-f]{4}[0-9a-f]{4}[0-9a-f]{4}[0-9a-f]{12})",
            url_or_id.replace("-", ""),
        )
        if match:
            raw = match.group(1)
            # Форматируем как стандартный UUID
            return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
        return url_or_id

    # ------------------------------------------------------------------
    # HTTP методы
    # ------------------------------------------------------------------

    def _get(self, path: str) -> Dict:
        resp = requests.get(
            f"{self.API_BASE}{path}", headers=self.headers, timeout=30
        )
        self._raise_for_status(resp)
        return resp.json()

    def _post(self, path: str, body: Dict) -> Dict:
        resp = requests.post(
            f"{self.API_BASE}{path}", headers=self.headers, json=body, timeout=30
        )
        self._raise_for_status(resp)
        return resp.json()

    def _patch(self, path: str, body: Dict) -> Dict:
        resp = requests.patch(
            f"{self.API_BASE}{path}", headers=self.headers, json=body, timeout=30
        )
        self._raise_for_status(resp)
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: requests.Response) -> None:
        if not resp.ok:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(
                f"Notion API error {resp.status_code}: {detail}"
            )


# ---------------------------------------------------------------------------
# MCPClient — единая точка входа
# ---------------------------------------------------------------------------

class MCPClient:
    """
    Единая точка входа для всех MCP провайдеров.

    Использование:
        mcp = MCPClient()
        mcp.notion.create_page(...)
        mcp.tool_call("notion-create-pages", {...})

    Добавление нового провайдера:
        self.my_tool = MyNewMCP(os.getenv("MY_TOOL_TOKEN"))
        # затем вызов: mcp.my_tool.some_method(...)
    """

    def __init__(self):
        notion_token = os.getenv("NOTION_TOKEN")
        if notion_token:
            self.notion = NotionMCP(notion_token)
            logger.info("MCPClient: NotionMCP инициализирован")
        else:
            self.notion = None
            logger.warning("MCPClient: NOTION_TOKEN не задан, NotionMCP недоступен")

        # Сюда добавляем новые провайдеры:
        # self.calendar = GoogleCalendarMCP(os.getenv("GOOGLE_TOKEN"))
        # self.slack    = SlackMCP(os.getenv("SLACK_TOKEN"))

    def tool_call(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """
        Универсальный вызов инструмента по имени.
        Маршрутизирует к нужному провайдеру по префиксу имени.
        """
        if tool_name.startswith("notion-"):
            if not self.notion:
                raise RuntimeError("NotionMCP недоступен: NOTION_TOKEN не задан")
            return self.notion.tool_call(tool_name, params)

        # Будущие провайдеры:
        # if tool_name.startswith("calendar-"):
        #     return self.calendar.tool_call(tool_name, params)

        raise ValueError(f"MCPClient: неизвестный инструмент '{tool_name}'")


# ---------------------------------------------------------------------------
# Глобальный экземпляр (ленивая инициализация)
# ---------------------------------------------------------------------------

_mcp_instance: Optional[MCPClient] = None


def get_mcp() -> MCPClient:
    """Возвращает глобальный экземпляр MCPClient (singleton)."""
    global _mcp_instance
    if _mcp_instance is None:
        _mcp_instance = MCPClient()
    return _mcp_instance
