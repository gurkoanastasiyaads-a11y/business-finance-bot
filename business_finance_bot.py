import os
import logging
import base64
import json
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
from groq import Groq

load_dotenv()

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("BUSINESS_FINANCE_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

ALLOWED_USERS = [451779172]

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

DB_PATH = "business_finance.db"
MAX_HISTORY = 300

SYSTEM_PROMPT = """Ты — бизнес-финансовый ассистент для ShowerZen™ — дропшиппинг бизнеса Анастасии.

--- О БИЗНЕСЕ ---
ShowerZen™ — премиальный фильтр для душа, продаётся через дропшиппинг в США и Tier-1 странах.
Цена продажи: $90 за единицу.
Основные рынки: США, Канада, Великобритания, Австралия.

--- ВАЛЮТЫ ---
Принимай записи в любой валюте, всегда конвертируй в USD (основная валюта бизнеса) и RUB.
Курсы (приблизительные):
- 1 USD ≈ 90 ₽
- 1 EUR ≈ 97 ₽
- 1 GBP ≈ 113 ₽

--- КАТЕГОРИИ РАСХОДОВ БИЗНЕСА ---
- Реклама (Facebook Ads, TikTok Ads, Instagram Ads)
- Инструменты и сервисы (Shopify, приложения, подписки)
- Контент (съёмка, дизайн, монтаж)
- Товар и логистика (закупка, доставка)
- Налоги и сборы
- Обучение и консалтинг
- Прочее

--- КАК РАБОТАТЬ ---
1. Записывай все расходы и доходы с датой и категорией
2. Всегда показывай сумму в USD и RUB
3. Отслеживай ROI: сколько вложено, сколько получено, какая прибыль
4. По запросу показывай: нужно ли ещё заработать чтобы выйти в плюс
5. Умей считать налоги когда просят (стандартные ставки США: self-employment tax ~15.3%, federal income tax от 10% до 37%)

--- ФОРМАТ ЗАПИСИ ---
При каждой записи отвечай:
✅ Записано: [категория]
[сумма в оригинальной валюте] = $X / X ₽
[тип: расход/доход]
📊 Текущий баланс: доходы $X — расходы $X = [прибыль/убыток] $X

--- СТАТИСТИКА ПО ЗАПРОСУ ---
📊 ShowerZen Финансы — [период]

💰 ДОХОДЫ: $X (X ₽)
💸 РАСХОДЫ: $X (X ₽)
📈 ПРИБЫЛЬ/УБЫТОК: $X (X ₽)
🎯 ROI: X%

Расходы по категориям:
• Реклама: $X (X%)
• Сервисы: $X (X%)
...

⚡ Чтобы выйти в ноль нужно продать ещё X единиц товара
💡 Аналитика и рекомендации: [конкретные советы]

--- НАЛОГИ ---
Когда просят посчитать налоги:
- Уточни систему налогообложения если не знаешь
- Используй актуальные ставки США по умолчанию
- Всегда предупреждай что это приблизительный расчёт и нужен бухгалтер

--- ВАЖНО ---
- Помни все записи из истории чата
- Всегда указывай дату
- Отвечай на русском языке
- Будь аналитичной и конкретной — это бизнес, нужны точные цифры"""


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_history (
        chat_id INTEGER PRIMARY KEY, history TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    conn.close()
    print("✅ Business finance bot DB initialized")


def load_history(chat_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT history FROM chat_history WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return json.loads(row[0]) if row else []


def save_history(chat_id, history):
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""INSERT INTO chat_history (chat_id, history, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id) DO UPDATE SET history=excluded.history, updated_at=CURRENT_TIMESTAMP""",
        (chat_id, json.dumps(history, ensure_ascii=False)))
    conn.commit()
    conn.close()


def clear_history(chat_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM chat_history WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()


def is_allowed(update):
    return update.effective_chat.id in ALLOWED_USERS


async def send_long(update, text):
    if len(text) > 4000:
        for part in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "Привет! Я финансовый ассистент ShowerZen™ 📊\n\n"
        "Веду учёт всех доходов и расходов бизнеса, считаю прибыль и ROI.\n\n"
        "Например:\n"
        "• «Потратила $200 на Facebook Ads»\n"
        "• «Продажи за неделю $450»\n"
        "• «Подписка Shopify $29»\n\n"
        "Команды:\n"
        "/stats — полная финансовая сводка\n"
        "/roi — текущий ROI и прибыль\n"
        "/tax — расчёт налогов\n"
        "/clear — очистить историю\n\n"
        "Понимаю фото счетов и голосовые! 🖼🎤"
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    now = datetime.now()
    month_name = now.strftime("%B %Y")
    history = load_history(chat_id)
    prompt = f"Дай полную финансовую сводку ShowerZen за {month_name}: все доходы и расходы по категориям в USD и рублях, прибыль, ROI, сколько единиц нужно продать чтобы выйти в ноль, и рекомендации."
    history.append({"role": "user", "content": prompt})
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5", max_tokens=3000,
            system=SYSTEM_PROMPT, messages=history
        )
        reply = response.content[0].text
        history.append({"role": "assistant", "content": reply})
        save_history(chat_id, history)
        await send_long(update, reply)
    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text("Что-то пошло не так 🙏")


async def roi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    history = load_history(chat_id)
    prompt = "Покажи текущий ROI бизнеса: сколько вложено всего, сколько получено, какая прибыль или убыток, и сколько единиц товара нужно продать чтобы выйти в плюс."
    history.append({"role": "user", "content": prompt})
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5", max_tokens=1500,
            system=SYSTEM_PROMPT, messages=history
        )
        reply = response.content[0].text
        history.append({"role": "assistant", "content": reply})
        save_history(chat_id, history)
        await send_long(update, reply)
    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text("Что-то пошло не так 🙏")


async def tax(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    history = load_history(chat_id)
    prompt = "Посчитай приблизительные налоги на основе текущих доходов бизнеса. Используй стандартные ставки США. Напомни что нужен бухгалтер для точного расчёта."
    history.append({"role": "user", "content": prompt})
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5", max_tokens=1500,
            system=SYSTEM_PROMPT, messages=history
        )
        reply = response.content[0].text
        history.append({"role": "assistant", "content": reply})
        save_history(chat_id, history)
        await send_long(update, reply)
    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text("Что-то пошло не так 🙏")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    clear_history(update.effective_chat.id)
    await update.message.reply_text("🗑 История очищена!")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    now = datetime.now().strftime("%d.%m.%Y")
    history = load_history(chat_id)
    history.append({"role": "user", "content": f"[{now}] {update.message.text}"})
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5", max_tokens=1000,
            system=SYSTEM_PROMPT, messages=history
        )
        reply = response.content[0].text
        history.append({"role": "assistant", "content": reply})
        save_history(chat_id, history)
        await update.message.reply_text(reply)
    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text("Что-то пошло не так 🙏")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_bytes = await file.download_as_bytearray()
    image_data = base64.standard_b64encode(bytes(file_bytes)).decode("utf-8")
    now = datetime.now().strftime("%d.%m.%Y")
    caption = update.message.caption or f"[{now}] Это счёт или чек бизнес-расходов. Распознай сумму, определи категорию и запиши."
    history = load_history(chat_id)
    history.append({"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
        {"type": "text", "text": caption}
    ]})
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5", max_tokens=1000,
            system=SYSTEM_PROMPT, messages=history
        )
        reply = response.content[0].text
        history[-1] = {"role": "user", "content": f"[{now}] [Фото счёта] {caption}"}
        history.append({"role": "assistant", "content": reply})
        save_history(chat_id, history)
        await update.message.reply_text(reply)
    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text("Что-то пошло не так с фото 🙏")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    try:
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        file_bytes = await file.download_as_bytearray()
        transcription = groq_client.audio.transcriptions.create(
            file=("voice.ogg", bytes(file_bytes), "audio/ogg"),
            model="whisper-large-v3",
            language="ru"
        )
        recognized_text = transcription.text
        now = datetime.now().strftime("%d.%m.%Y")
        history = load_history(chat_id)
        history.append({"role": "user", "content": f"[{now}] {recognized_text}"})
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5", max_tokens=1000,
            system=SYSTEM_PROMPT, messages=history
        )
        reply = response.content[0].text
        history.append({"role": "assistant", "content": reply})
        save_history(chat_id, history)
        await update.message.reply_text(f"🎤 «{recognized_text}»\n\n{reply}")
    except Exception as e:
        print(f"Voice error: {e}")
        await update.message.reply_text("Не смогла распознать голосовое 🙏")


def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("roi", roi))
    app.add_handler(CommandHandler("tax", tax))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("📊 Business finance bot started!")
    app.run_polling(stop_signals=None)


if __name__ == "__main__":
    main()
