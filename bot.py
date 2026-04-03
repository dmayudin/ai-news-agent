#!/usr/bin/env python3
"""
AI News Bot v2 — Telegram бот с natural language chat, inline-кнопками и Mini App.
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from html import escape as html_escape
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv('/opt/ai-news-agent/.env')

from llm_client import chat_complete
from news_agent import NewsAgent, html_escape, source_link

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/ai-news-bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

BOT_TOKEN  = os.getenv('TELEGRAM_BOT_TOKEN', '')
USER_ID    = int(os.getenv('TELEGRAM_USER_ID', '0'))
CHANNEL_ID = os.getenv('CHANNEL_ID', '@ai_is_you')
WEBAPP_URL = os.getenv('WEBAPP_URL', '')

agent = NewsAgent(
    openai_api_key = os.getenv('OPENAI_API_KEY', ''),
    telegram_token = BOT_TOKEN,
    user_id        = str(USER_ID),
)

pending_digests: dict = {}
chat_history: list = []
CHAT_HISTORY_LIMIT = 10

# ─── Telegram API ─────────────────────────────────────────────────────────────

def tg(method: str, **kwargs) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, json=kwargs, timeout=15)
        return r.json()
    except Exception as e:
        logger.error(f"Telegram API error ({method}): {e}")
        return {}


def send(chat_id, text: str, reply_markup=None, parse_mode: str = "HTML") -> dict:
    kwargs = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    return tg("sendMessage", **kwargs)


def edit_msg(chat_id, message_id, text: str, reply_markup=None):
    kwargs = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    return tg("editMessageText", **kwargs)


def answer_cb(callback_query_id, text: str = ""):
    tg("answerCallbackQuery", callback_query_id=callback_query_id, text=text)


def send_action(chat_id, action="typing"):
    tg("sendChatAction", chat_id=chat_id, action=action)


# ─── Клавиатуры ───────────────────────────────────────────────────────────────

def main_kb():
    buttons = [
        [
            {"text": "📰 Новости сейчас",    "callback_data": "cmd_news"},
            {"text": "✍️ Написать пост",      "callback_data": "cmd_post"},
        ],
        [
            {"text": "📊 Дайджест в канал",   "callback_data": "cmd_digest"},
            {"text": "📅 Идеи в Notion",      "callback_data": "cmd_notion"},
        ],
        [
            {"text": "⚙️ Статус системы",     "callback_data": "cmd_status"},
            {"text": "🧹 Очистить чат",        "callback_data": "cmd_clear"},
        ],
    ]
    if WEBAPP_URL:
        tma_url = WEBAPP_URL.rstrip('/') + '/tma'
        buttons.append([
            {"text": "YDA News App", "web_app": {"url": tma_url}}
        ])
    return {"inline_keyboard": buttons}


def approve_kb(key: str):
    return {"inline_keyboard": [[
        {"text": "✅ Опубликовать",  "callback_data": f"approve_{key}"},
        {"text": "✏️ Переписать",    "callback_data": f"rewrite_{key}"},
        {"text": "❌ Отменить",      "callback_data": f"cancel_{key}"},
    ]]}


def news_item_kb(link: str, idx: int):
    return {"inline_keyboard": [
        [{"text": "🔗 Читать полностью", "url": link}],
        [{"text": "✍️ Написать пост об этом", "callback_data": f"post_about_{idx}"}],
    ]}


# ─── Генерация контента ───────────────────────────────────────────────────────

def generate_short_post(news_items: list) -> str:
    if not news_items:
        return "Свежих новостей об ИИ пока нет."
    news_text = "\n".join(
        f"{i}. {item['title']}" for i, item in enumerate(news_items[:10], 1)
    )
    system_prompt = """Ты — Дмитрий Юдин. Руководитель ИИ-направления Cloud.ru, автор канала @ai_is_you.
Пишешь живо, по-человечески — как объясняешь коллеге за кофе.

Правила:
— Начинай с «На связи Дима Юдин»
— Первая содержательная строка — удар без вводных: тезис, парадокс или неожиданный угол
— Разговорные конструкции: «ну ок», «честно говоря», «по факту», «вот в чём штука»
— Короткие абзацы — 1–3 предложения
— Конкретика: цифры, названия компаний, продуктов
— Скептицизм к хайпу — твоя фирменная черта
— Финал — вывод или наблюдение. Никаких вопросов к аудитории
— Ноль эмодзи. Ноль хэштегов. Ноль маркированных списков
— ПЕРЕВОДИ все заголовки на русский язык"""
    prompt = f"""Напиши короткий авторский пост (150–200 слов) по следующим новостям об ИИ.
Выбери 1–2 самых важных события, добавь свой угол. Не пересказывай заголовки.
Начни с «На связи Дима Юдин», потом сразу в суть.

НОВОСТИ:
{news_text}"""
    try:
        return chat_complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": prompt}
            ],
            temperature=0.8,
            max_tokens=500
        )
    except Exception as e:
        logger.error(f"LLM short_post: {e}")
        return f"Ошибка генерации поста: {e}"


def generate_digest(news_items: list) -> str:
    if not news_items:
        return "Свежих новостей об ИИ пока нет."
    today     = datetime.now().strftime('%d.%m.%Y')
    news_text = "\n".join(
        f"{i}. {item['title']}" for i, item in enumerate(news_items[:12], 1)
    )
    system_prompt = """Ты — Дмитрий Юдин. Руководитель ИИ-направления Cloud.ru, автор канала @ai_is_you.
Пишешь дайджест как живой человек — не как новостной агрегатор.
Каждый тезис — суть + твой угол. Разговорный тон, конкретика, скептицизм к хайпу.
Ноль эмодзи. Ноль хэштегов. ПЕРЕВОДИ заголовки на русский."""
    prompt = f"""Создай дайджест новостей об ИИ за {today}.

Формат (строго):
Строка 1: На связи Дима Юдин. Вот что важного произошло в ИИ сегодня.
Строка 2: пустая
Строки 3–N: тезисы. Каждый — ОДНА строка: номер, точка, пробел, предложение.
  Тезис пиши живо: не «Компания X выпустила Y», а что это реально значит.
  5–7 тезисов, только самые важные.
После тезисов: пустая строка
Последняя строка: @ai_is_you

НЕ добавляй ссылки — они вставятся автоматически.

НОВОСТИ:
{news_text}"""
    try:
        raw = chat_complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": prompt}
            ],
            temperature=0.75,
            max_tokens=700
        )
    except Exception as e:
        logger.error(f"LLM digest: {e}")
        return f"Ошибка генерации дайджеста: {e}"

    lines = raw.split('\n')
    result_lines = []
    news_idx = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result_lines.append('')
            continue
        if re.match(r'^\d+\.', stripped) and news_idx < len(news_items):
            link = news_items[news_idx]['link']
            result_lines.append(f"{html_escape(stripped)}  {source_link(link)}")
            news_idx += 1
        else:
            result_lines.append(html_escape(stripped))
    return '\n'.join(result_lines)


# ─── Обработчики ─────────────────────────────────────────────────────────────

def handle_start(chat_id):
    text = (
        "👋 <b>AI News Bot v2</b>\n\n"
        "Слежу за новостями об ИИ из 9 источников, перевожу на русский и помогаю создавать контент.\n\n"
        "<b>Умею:</b>\n"
        "• Собирать и анализировать новости\n"
        "• Переводить заголовки на русский\n"
        "• Писать посты и дайджесты в стиле @ai_is_you\n"
        "• Отвечать на вопросы об ИИ\n\n"
        "Выбери действие или просто напиши мне что-нибудь 👇"
    )
    send(chat_id, text, reply_markup=main_kb())


def handle_status(chat_id, cb_id=None):
    if cb_id:
        answer_cb(cb_id, "Проверяю...")
    send_action(chat_id)
    try:
        chat_complete([{"role": "user", "content": "ping"}], temperature=0, max_tokens=5)
        llm_status = "✅ LLM работает"
    except Exception as e:
        llm_status = f"❌ LLM: {str(e)[:50]}"
    me = tg("getMe")
    tg_status = f"✅ @{me['result']['username']}" if me.get("ok") else "❌ Telegram API"
    text = (
        f"<b>⚙️ Статус системы</b>\n\n"
        f"{tg_status}\n"
        f"{llm_status}\n\n"
        f"<b>Канал:</b> {CHANNEL_ID}\n"
        f"<b>Расписание:</b> 09:00, 16:00, 20:00 МСК\n"
        f"<b>История чата:</b> {len(chat_history)} сообщений"
    )
    send(chat_id, text, reply_markup=main_kb())


def handle_news(chat_id, cb_id=None):
    if cb_id:
        answer_cb(cb_id, "Собираю новости...")
    send_action(chat_id)
    send(chat_id, "🔍 Собираю свежие новости об ИИ...")
    try:
        news = agent.fetch_news(hours_back=24) or agent.fetch_news(hours_back=72)
        if not news:
            send(chat_id, "😔 Новостей не найдено. Попробуйте позже.", reply_markup=main_kb())
            return
        send_action(chat_id)
        send(chat_id, f"✅ Нашёл {len(news)} новостей. Анализирую через GPT...")
        ai_analysis = agent.analyze_with_ai(news)
        now = datetime.now().strftime('%d.%m.%Y %H:%M')
        msg = (
            f"<b>🤖 AI-СВОДКА НОВОСТЕЙ</b>\n"
            f"<i>{now} МСК · {len(news)} новостей</i>\n"
            f"{'─' * 28}\n\n"
            f"{html_escape(ai_analysis)}"
        )
        send(chat_id, msg)

        # Топ-5 новостей с кнопками
        send(chat_id, "<b>📋 ТОП-5 НОВОСТЕЙ:</b>")
        for i, item in enumerate(news[:5], 1):
            title_ru = agent.translate_title(item.get('title', ''))
            title_orig = html_escape(item.get('title', ''))
            src  = html_escape(item.get('source', ''))
            date = item.get('published', '')
            link = item.get('link', '')
            if title_ru != item.get('title', ''):
                entry = f"<b>{html_escape(title_ru)}</b>\n<i>{title_orig}</i>\n<i>{src}"
            else:
                entry = f"<b>{title_orig}</b>\n<i>{src}"
            if date:
                entry += f" · {date}"
            entry += "</i>"
            send(chat_id, entry, reply_markup=news_item_kb(link, i - 1))

        send(chat_id, "Что дальше?", reply_markup=main_kb())
    except Exception as e:
        logger.error(f"handle_news: {e}")
        send(chat_id, f"❌ Ошибка: {html_escape(str(e))}", reply_markup=main_kb())


def handle_post(chat_id, cb_id=None, news_idx=None):
    if cb_id:
        answer_cb(cb_id, "Генерирую пост...")
    send_action(chat_id)
    send(chat_id, "✍️ Генерирую пост для канала...")
    try:
        news = agent.fetch_news(hours_back=24) or agent.fetch_news(hours_back=72)
        if not news:
            send(chat_id, "😔 Новостей не найдено.", reply_markup=main_kb())
            return
        if news_idx is not None and 0 <= news_idx < len(news):
            post_news = [news[news_idx]]
        else:
            post_news = news
        send_action(chat_id)
        post_text = generate_short_post(post_news)
        key = str(int(time.time()))
        pending_digests[key] = post_text
        preview = html_escape(post_text[:3500])
        send(
            chat_id,
            f"<b>✍️ Готовый пост:</b>\n\n{preview}\n\n<i>Опубликовать в {CHANNEL_ID}?</i>",
            reply_markup=approve_kb(key)
        )
    except Exception as e:
        logger.error(f"handle_post: {e}")
        send(chat_id, f"❌ Ошибка: {html_escape(str(e))}", reply_markup=main_kb())


def handle_digest(chat_id, cb_id=None):
    if cb_id:
        answer_cb(cb_id, "Готовлю дайджест...")
    send_action(chat_id)
    send(chat_id, "📊 Готовлю дайджест для канала...")
    try:
        news = agent.fetch_news(hours_back=8) or agent.fetch_news(hours_back=48)
        if not news:
            send(chat_id, "😔 Новостей не найдено.", reply_markup=main_kb())
            return
        send_action(chat_id)
        digest_html = generate_digest(news)
        key = str(int(time.time()))
        pending_digests[key] = digest_html
        preview = (
            f"<b>ПРЕВЬЮ ДАЙДЖЕСТА ДЛЯ {CHANNEL_ID}</b>\n{'─' * 30}\n\n"
            f"{digest_html}\n\n{'─' * 30}\nОпубликовать в канал?"
        )
        send(chat_id, preview, reply_markup=approve_kb(key))
    except Exception as e:
        logger.error(f"handle_digest: {e}")
        send(chat_id, f"❌ Ошибка: {html_escape(str(e))}", reply_markup=main_kb())


def handle_notion(chat_id, cb_id=None):
    if cb_id:
        answer_cb(cb_id, "Генерирую идеи...")
    send(chat_id, "📅 Анализирую тренды и генерирую идеи для контент-плана...")
    try:
        from notion_sync import sync_ideas_to_notion, format_notion_report
        news = agent.fetch_news(hours_back=24) or agent.fetch_news(hours_back=72)
        if not news:
            send(chat_id, "Новостей не найдено. Попробуйте позже.", reply_markup=main_kb())
            return
        result = sync_ideas_to_notion(news)
        report = format_notion_report(result)
        send(chat_id, report, reply_markup=main_kb())
    except Exception as e:
        logger.error(f"handle_notion: {e}")
        send(chat_id, f"❌ Ошибка: {html_escape(str(e))}", reply_markup=main_kb())


def handle_approve(chat_id, message_id, key, cb_id):
    answer_cb(cb_id, "Публикую...")
    text = pending_digests.pop(key, None)
    if not text:
        edit_msg(chat_id, message_id, "❌ Пост не найден или уже опубликован.")
        return
    result = tg("sendMessage", chat_id=CHANNEL_ID, text=text,
                parse_mode="HTML", disable_web_page_preview=True)
    if result.get("ok"):
        edit_msg(chat_id, message_id, f"✅ Опубликовано в {CHANNEL_ID}!")
        send(chat_id, "Что-то ещё?", reply_markup=main_kb())
    else:
        err = html_escape(result.get("description", "неизвестная ошибка"))
        edit_msg(chat_id, message_id, f"❌ Ошибка публикации: {err}")


def handle_rewrite(chat_id, message_id, key, cb_id):
    answer_cb(cb_id, "Переписываю...")
    pending_digests.pop(key, None)
    threading.Thread(target=handle_post, args=(chat_id,), daemon=True).start()


def handle_cancel(chat_id, message_id, key, cb_id):
    answer_cb(cb_id, "Отменено")
    pending_digests.pop(key, None)
    edit_msg(chat_id, message_id, "❌ Публикация отменена.")
    send(chat_id, "Что-то ещё?", reply_markup=main_kb())


# ─── Natural Language Chat ────────────────────────────────────────────────────

def handle_chat(chat_id, user_text: str):
    global chat_history
    send_action(chat_id)
    chat_history.append({"role": "user", "content": user_text})
    if len(chat_history) > CHAT_HISTORY_LIMIT:
        chat_history = chat_history[-CHAT_HISTORY_LIMIT:]

    system_prompt = (
        "Ты AI News Bot — умный ассистент, который следит за новостями об ИИ. "
        "Ты работаешь в Telegram и помогаешь пользователю:\n"
        "- Отвечаешь на вопросы об ИИ, нейросетях, технологиях\n"
        "- Объясняешь термины и концепции простым языком\n"
        "- Помогаешь создавать контент\n"
        "- Анализируешь тренды\n\n"
        "Отвечай кратко (2-4 абзаца), по делу, на отличном русском языке. "
        "Если пользователь хочет посмотреть новости, написать пост или дайджест — "
        "скажи что нужно нажать кнопку в меню. "
        "Используй эмодзи умеренно."
    )
    try:
        messages = [{"role": "system", "content": system_prompt}] + chat_history
        response = chat_complete(messages=messages, temperature=0.8, max_tokens=600)
        chat_history.append({"role": "assistant", "content": response})
        send(chat_id, html_escape(response), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"handle_chat error: {e}")
        send(chat_id, "😔 Не удалось получить ответ. Попробуйте ещё раз.", reply_markup=main_kb())


# ─── Обработка обновлений ─────────────────────────────────────────────────────

def process_update(update: dict):
    try:
        if "callback_query" in update:
            cq      = update["callback_query"]
            cb_id   = cq["id"]
            data    = cq.get("data", "")
            chat_id = cq["message"]["chat"]["id"]
            msg_id  = cq["message"]["message_id"]
            if cq["from"]["id"] != USER_ID:
                answer_cb(cb_id, "Нет доступа")
                return
            if   data == "cmd_news":
                threading.Thread(target=handle_news,   args=(chat_id, cb_id), daemon=True).start()
            elif data == "cmd_post":
                threading.Thread(target=handle_post,   args=(chat_id, cb_id), daemon=True).start()
            elif data == "cmd_digest":
                threading.Thread(target=handle_digest, args=(chat_id, cb_id), daemon=True).start()
            elif data == "cmd_notion":
                threading.Thread(target=handle_notion, args=(chat_id, cb_id), daemon=True).start()
            elif data == "cmd_status":
                threading.Thread(target=handle_status, args=(chat_id, cb_id), daemon=True).start()
            elif data == "cmd_clear":
                global chat_history
                chat_history = []
                answer_cb(cb_id, "История очищена")
                send(chat_id, "🧹 История диалога очищена.", reply_markup=main_kb())
            elif data.startswith("approve_"):
                key = data[len("approve_"):]
                threading.Thread(target=handle_approve, args=(chat_id, msg_id, key, cb_id), daemon=True).start()
            elif data.startswith("rewrite_"):
                key = data[len("rewrite_"):]
                threading.Thread(target=handle_rewrite, args=(chat_id, msg_id, key, cb_id), daemon=True).start()
            elif data.startswith("cancel_"):
                key = data[len("cancel_"):]
                handle_cancel(chat_id, msg_id, key, cb_id)
            elif data.startswith("post_about_"):
                idx = int(data[len("post_about_"):])
                answer_cb(cb_id, "Пишу пост...")
                threading.Thread(target=handle_post, args=(chat_id, None, idx), daemon=True).start()
            else:
                answer_cb(cb_id)
            return

        if "message" not in update:
            return
        msg     = update["message"]
        chat_id = msg["chat"]["id"]
        from_id = msg.get("from", {}).get("id")
        text    = msg.get("text", "").strip()
        if from_id != USER_ID:
            return
        if   text in ("/start", "/menu"):
            handle_start(chat_id)
        elif text == "/news":
            threading.Thread(target=handle_news,   args=(chat_id,), daemon=True).start()
        elif text == "/post":
            threading.Thread(target=handle_post,   args=(chat_id,), daemon=True).start()
        elif text == "/digest":
            threading.Thread(target=handle_digest, args=(chat_id,), daemon=True).start()
        elif text == "/notion":
            threading.Thread(target=handle_notion, args=(chat_id,), daemon=True).start()
        elif text == "/status":
            threading.Thread(target=handle_status, args=(chat_id,), daemon=True).start()
        elif text == "/clear":
            chat_history = []
            send(chat_id, "🧹 История диалога очищена.", reply_markup=main_kb())
        elif text:
            threading.Thread(target=handle_chat, args=(chat_id, text), daemon=True).start()
    except Exception as e:
        logger.error(f"process_update error: {e}")


# ─── Polling ──────────────────────────────────────────────────────────────────

def run_polling():
    logger.info("Бот запущен, начинаю polling...")
    offset = None
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset
            r    = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params=params, timeout=40
            )
            data = r.json()
            if not data.get("ok"):
                logger.error(f"getUpdates error: {data}")
                time.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                process_update(upd)
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    logger.info(f"AI News Bot v2 запущен. USER_ID={USER_ID}, CHANNEL={CHANNEL_ID}")
    run_polling()
