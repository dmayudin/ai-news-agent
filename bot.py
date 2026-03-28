#!/usr/bin/env python3
"""
AI News Bot — Telegram бот с интерактивными кнопками
Команды: /start /news /post /digest
"""

import os
import re
import sys
import logging
import threading
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv('/opt/ai-news-agent/.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/ai-news-bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

sys.path.insert(0, '/opt/ai-news-agent')
from news_agent import NewsAgent, html_escape, source_link

BOT_TOKEN  = os.getenv('TELEGRAM_BOT_TOKEN')
USER_ID    = int(os.getenv('TELEGRAM_USER_ID'))
OPENAI_KEY = os.getenv('OPENAI_API_KEY')
CHANNEL_ID = '@ai_is_you'

pending_digests: dict = {}

agent = NewsAgent(OPENAI_KEY, BOT_TOKEN, str(USER_ID))


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


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def main_kb():
    return {
        "inline_keyboard": [
            [
                {"text": "Полная сводка",    "callback_data": "cmd_news"},
                {"text": "Короткий пост",    "callback_data": "cmd_post"}
            ],
            [
                {"text": "Дайджест в канал", "callback_data": "cmd_digest"}
            ]
        ]
    }


def approve_kb(key: str):
    return {
        "inline_keyboard": [[
            {"text": "Опубликовать в канал", "callback_data": f"approve_{key}"},
            {"text": "Отмена",               "callback_data": f"cancel_{key}"}
        ]]
    }


# ─── AI генерация ─────────────────────────────────────────────────────────────

def generate_short_post(news_items: list) -> str:
    """
    Короткий авторский пост для Telegram-канала.
    Принципы: конкретный заголовок, 2-3 события, вывод, без эмодзи и хэштегов.
    """
    if not news_items:
        return "Свежих новостей об ИИ пока нет."

    news_text = "\n".join(
        f"{i}. {item['title']}" for i, item in enumerate(news_items[:10], 1)
    )

    prompt = f"""Ты редактор Telegram-канала об искусственном интеллекте.
Напиши короткий авторский пост (150–200 слов) по следующим новостям.

Правила:
— Никаких эмодзи
— Никаких хэштегов
— Никаких маркированных списков со звёздочками или тире
— Заголовок: конкретный, фактический, одна строка, ЗАГЛАВНЫМИ БУКВАМИ
— Затем 2–3 абзаца: каждый — одна мысль, одно событие, одно следствие
— Финальный абзац: краткий вывод или вопрос к читателю
— Язык: русский, стиль делового медиа (РБК, Коммерсантъ)

НОВОСТИ:
{news_text}"""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_KEY)
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Ты редактор делового Telegram-канала об ИИ. Пишешь без эмодзи, конкретно и по делу."},
                {"role": "user",   "content": prompt}
            ],
            temperature=0.7,
            max_tokens=450
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI short_post: {e}")
        return f"Ошибка генерации поста: {e}"


def generate_digest(news_items: list) -> str:
    """
    Дайджест для Telegram-канала.
    Структура: шапка / рубрики / тезисы / подпись.
    Без эмодзи. Ссылки — гиперссылки 'Источник'.
    """
    if not news_items:
        return "Свежих новостей об ИИ пока нет."

    today     = datetime.now().strftime('%d.%m.%Y')
    news_text = "\n".join(
        f"{i}. {item['title']}" for i, item in enumerate(news_items[:12], 1)
    )

    prompt = f"""Ты редактор делового Telegram-канала об искусственном интеллекте.
Создай дайджест новостей за {today}.

Правила форматирования (строго):
— Никаких эмодзи
— Никаких хэштегов
— Никаких маркированных списков со звёздочками или тире
— Строка 1: «AI ДАЙДЖЕСТ — {today}» (заглавными, без кавычек)
— Строка 2: пустая
— Строка 3: рубрика — одно слово заглавными, например «ИССЛЕДОВАНИЯ» или «ИНДУСТРИЯ» или «ПРОДУКТЫ» (выбери наиболее подходящую по смыслу)
— Затем 5–7 тезисов. Каждый тезис — ОДНА строка: порядковый номер, точка, пробел, конкретное предложение.
  Тезис должен содержать: субъект + действие + результат. Не пересказывай заголовок — добавь контекст.
— После всех тезисов пустая строка
— Последняя строка: «@ai_is_you»
— НЕ добавляй ссылки — они будут вставлены автоматически

НОВОСТИ:
{news_text}"""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_KEY)
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Ты редактор делового Telegram-канала об ИИ. Пишешь без эмодзи, строго по формату, конкретно."},
                {"role": "user",   "content": prompt}
            ],
            temperature=0.4,
            max_tokens=700
        )
        raw = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI digest: {e}")
        return f"Ошибка генерации дайджеста: {e}"

    # Вставляем гиперссылки «Источник» после каждого тезиса
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


# ─── Форматирование сводки (без эмодзи) ──────────────────────────────────────

def format_full_digest(news_items: list, ai_analysis: str) -> list[str]:
    """Возвращает список сообщений для полной сводки без эмодзи."""
    now = datetime.now().strftime('%d.%m.%Y %H:%M')
    messages = []

    # Блок 1: AI-анализ
    msg = (
        f"<b>AI-АНАЛИЗ НОВОСТЕЙ ОБ ИИ</b>\n"
        f"<i>{now} МСК</i>\n"
        f"{'─' * 30}\n\n"
        f"{html_escape(ai_analysis)}"
    )
    messages.append(msg)

    # Блок 2+: список новостей
    header = f"<b>НОВОСТИ ЗА ПЕРИОД — {len(news_items)} материалов</b>\n\n"
    current = header
    for i, item in enumerate(news_items[:15], 1):
        title  = html_escape(item['title'])
        src    = html_escape(item['source'])
        date   = item['published']
        link   = item['link']

        line = f"{i}. <b>{title}</b>\n"
        line += f"   <i>{src}"
        if date:
            line += f" · {date}"
        line += f"</i>  {source_link(link)}\n\n"

        if len(current) + len(line) > 3800:
            messages.append(current)
            current = ""
        current += line

    if current:
        messages.append(current)

    return messages


# ─── Обработчики ─────────────────────────────────────────────────────────────

def handle_start(chat_id):
    text = (
        "<b>AI News Agent</b>\n\n"
        "Собираю актуальные новости об искусственном интеллекте "
        "из 7 источников и отправляю сводки в <b>09:00, 16:00 и 20:00 МСК</b>.\n\n"
        "Выберите действие:"
    )
    send(chat_id, text, reply_markup=main_kb())


def handle_news(chat_id, cb_id=None):
    if cb_id:
        answer_cb(cb_id, "Собираю новости...")
    send(chat_id, "Собираю полную сводку новостей. Подождите 1–2 минуты...")
    try:
        news = agent.fetch_news(hours_back=8) or agent.fetch_news(hours_back=48)
        if not news:
            send(chat_id, "Свежих новостей не найдено. Попробуйте позже.", reply_markup=main_kb())
            return
        ai_analysis = agent.analyze_with_ai(news)
        for msg in format_full_digest(news, ai_analysis):
            send(chat_id, msg)
        send(chat_id, "Сводка сформирована.", reply_markup=main_kb())
    except Exception as e:
        logger.error(f"handle_news: {e}")
        send(chat_id, f"Ошибка: {html_escape(str(e))}", reply_markup=main_kb())


def handle_post(chat_id, cb_id=None):
    if cb_id:
        answer_cb(cb_id, "Генерирую пост...")
    send(chat_id, "Генерирую короткий пост, секунду...")
    try:
        news = agent.fetch_news(hours_back=8) or agent.fetch_news(hours_back=48)
        post_text = generate_short_post(news)
        # Пост — plain text от GPT, экранируем и отправляем
        send(chat_id, html_escape(post_text), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"handle_post: {e}")
        send(chat_id, f"Ошибка: {html_escape(str(e))}", reply_markup=main_kb())


def handle_digest(chat_id, cb_id=None):
    if cb_id:
        answer_cb(cb_id, "Готовлю дайджест...")
    send(chat_id, "Готовлю дайджест для канала, секунду...")
    try:
        news = agent.fetch_news(hours_back=8) or agent.fetch_news(hours_back=48)
        digest_html = generate_digest(news)

        key = str(int(time.time()))
        pending_digests[key] = digest_html

        preview = (
            f"<b>ПРЕВЬЮ ДАЙДЖЕСТА ДЛЯ КАНАЛА {CHANNEL_ID}</b>\n"
            f"{'─' * 30}\n\n"
            f"{digest_html}\n\n"
            f"{'─' * 30}\n"
            "Опубликовать в канал?"
        )
        send(chat_id, preview, reply_markup=approve_kb(key))
    except Exception as e:
        logger.error(f"handle_digest: {e}")
        send(chat_id, f"Ошибка: {html_escape(str(e))}", reply_markup=main_kb())


def handle_approve(chat_id, message_id, key, cb_id):
    answer_cb(cb_id, "Публикую...")
    digest_html = pending_digests.pop(key, None)
    if not digest_html:
        edit_msg(chat_id, message_id, "Дайджест не найден или уже опубликован.")
        return
    try:
        result = tg("sendMessage",
                    chat_id=CHANNEL_ID,
                    text=digest_html,
                    parse_mode="HTML",
                    disable_web_page_preview=True)
        if result.get("ok"):
            edit_msg(chat_id, message_id, f"Дайджест опубликован в <b>{CHANNEL_ID}</b>.")
            send(chat_id, "Что-то ещё?", reply_markup=main_kb())
        else:
            err = html_escape(result.get("description", "неизвестная ошибка"))
            edit_msg(chat_id, message_id,
                     f"Ошибка публикации: {err}\n\n"
                     "Убедитесь, что бот добавлен в канал как администратор.")
    except Exception as e:
        logger.error(f"handle_approve: {e}")
        edit_msg(chat_id, message_id, f"Ошибка: {html_escape(str(e))}")


def handle_cancel(chat_id, message_id, key, cb_id):
    answer_cb(cb_id, "Отменено")
    pending_digests.pop(key, None)
    edit_msg(chat_id, message_id, "Публикация отменена.")
    send(chat_id, "Что-то ещё?", reply_markup=main_kb())


# ─── Обработка обновлений ────────────────────────────────────────────────────

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
            elif data.startswith("approve_"):
                key = data[len("approve_"):]
                threading.Thread(target=handle_approve, args=(chat_id, msg_id, key, cb_id), daemon=True).start()
            elif data.startswith("cancel_"):
                key = data[len("cancel_"):]
                handle_cancel(chat_id, msg_id, key, cb_id)
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
        else:
            handle_start(chat_id)

    except Exception as e:
        logger.error(f"process_update error: {e}")


# ─── Polling ─────────────────────────────────────────────────────────────────

def run_polling():
    logger.info("Бот запущен, начинаю polling...")
    offset = None

    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset

            r = requests.get(
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
    logger.info(f"AI News Bot запущен. USER_ID={USER_ID}, CHANNEL={CHANNEL_ID}")
    run_polling()
