import os
import sqlite3
import asyncio
import logging
from contextlib import closing

from flask import Flask, request, abort, has_request_context
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

BOT_TOKEN = "8962802957:AAHmrkEy_vFPsnHMdvNQkC0VcRhuEWCu0y0"
DB_PATH = os.environ.get("DB_PATH", "dental_bot.db")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

SERVICES = ("crown", "implant", "bridge")


def _conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with closing(_conn()) as conn, conn:
        c = conn.cursor()
        c.execute(
            "CREATE TABLE IF NOT EXISTS prices ("
            "service TEXT PRIMARY KEY, price REAL NOT NULL)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS patients ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, "
            "service TEXT NOT NULL, "
            "tooth_color TEXT NOT NULL, "
            "count INTEGER NOT NULL, "
            "total_price REAL NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        for s in SERVICES:
            c.execute(
                "INSERT OR IGNORE INTO prices (service, price) VALUES (?, 0)",
                (s,),
            )


def db_get_price(service: str) -> float:
    with closing(_conn()) as conn:
        row = conn.execute(
            "SELECT price FROM prices WHERE service=?", (service,)
        ).fetchone()
    return row[0] if row else 0.0


def db_all_prices() -> dict:
    with closing(_conn()) as conn:
        rows = conn.execute("SELECT service, price FROM prices").fetchall()
    return {s: p for s, p in rows}


def db_set_price(service: str, price: float):
    with closing(_conn()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO prices (service, price) VALUES (?, ?)",
            (service, price),
        )


def db_add_patient(name, service, color, count, total):
    with closing(_conn()) as conn, conn:
        conn.execute(
            "INSERT INTO patients "
            "(name, service, tooth_color, count, total_price) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, service, color, count, total),
        )


def db_get_patients():
    with closing(_conn()) as conn:
        return conn.execute(
            "SELECT name, service, tooth_color, count, total_price, created_at "
            "FROM patients ORDER BY id DESC"
        ).fetchall()


def fmt_price(p: float) -> str:
    s = f"{p:.2f}"
    return s[:-3] if s.endswith(".00") else s


SERVICE_LABELS = {
    "crown": "👑 Crown",
    "implant": "🔩 Implant",
    "bridge": "🌉 Bridge",
}

MENU_BUTTONS_REGEX = "^(👥 Patients|🆕 New Patient|💰 Prices|🩺 Specialist Doctor|🤖 AI)$"

NEW_NAME, NEW_TYPE, NEW_COLOR, NEW_COUNT = range(4)
WAITING_PRICE = 10


def main_menu_kb():
    return ReplyKeyboardMarkup(
        [
            ["👥 Patients", "🆕 New Patient"],
            ["💰 Prices", "🩺 Specialist Doctor"],
            ["🤖 AI"],
        ],
        resize_keyboard=True,
    )


def service_inline_kb():
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("👑 Crown", callback_data="svc_crown"),
            InlineKeyboardButton("🔩 Implant", callback_data="svc_implant"),
            InlineKeyboardButton("🌉 Bridge", callback_data="svc_bridge"),
        ]]
    )


def price_inline_kb():
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("👑 Crown", callback_data="price_crown"),
            InlineKeyboardButton("🔩 Implant", callback_data="price_implant"),
            InlineKeyboardButton("🌉 Bridge", callback_data="price_bridge"),
        ]]
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦷 *Welcome to Dental Bot!*\n\nChoose an option from the menu below:",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦷 *Help*\n\n/start -> Main menu\n/cancel -> Cancel current action\n\n"
        "Use the menu buttons to navigate.",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Cancelled. Back to main menu.",
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


async def show_patients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_get_patients()
    if not rows:
        await update.message.reply_text("📭 No patients registered yet.")
        return

    lines = [f"👥 *Patients List* - {len(rows)} total\n"]
    for i, (name, service, color, count, total, _) in enumerate(rows, 1):
        lines.append(
            f"*{i}. {name}*\n"
            f"   🦷 {SERVICE_LABELS.get(service, service)} x {count}\n"
            f"   🎨 {color}\n"
            f"   💰 Total: {fmt_price(total)}\n"
        )

    full = "\n".join(lines)
    if len(full) <= 4000:
        await update.message.reply_text(full, parse_mode="Markdown")
        return

    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 4000:
            await update.message.reply_text(chunk, parse_mode="Markdown")
            chunk = ""
        chunk += line + "\n"
    if chunk:
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def show_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = db_all_prices()
    text = (
        "💰 *Prices*\n\n"
        f"👑 Crown:   {fmt_price(c.get('crown', 0))}\n"
        f"🔩 Implant: {fmt_price(c.get('implant', 0))}\n"
        f"🌉 Bridge:  {fmt_price(c.get('bridge', 0))}\n\n"
        "Select a service to update its price:"
    )
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=price_inline_kb()
    )


async def price_service_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    service = q.data.replace("price_", "")
    context.user_data["pending_service"] = service
    await q.edit_message_text(
        f"💵 Current *{SERVICE_LABELS[service]}* price: "
        f"{fmt_price(db_get_price(service))}\n\n"
        "Send the new price (number only):",
        parse_mode="Markdown",
    )
    return WAITING_PRICE


async def price_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    service = context.user_data.get("pending_service")
    if not service:
        await update.message.reply_text(
            "⚠️ No service selected. Use 💰 Prices again.",
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END

    txt = update.message.text.strip().replace(",", "")
    try:
        price = float(txt)
        if price < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Send a positive number:")
        return WAITING_PRICE

    db_set_price(service, price)
    context.user_data.pop("pending_service", None)
    await update.message.reply_text(
        f"✅ *{SERVICE_LABELS[service]}* price set to {fmt_price(price)}.",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


async def new_patient_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_patient"] = {}
    await update.message.reply_text(
        "🆕 *New Patient*\n\nStep 1/4 - Enter the patient's name:",
        parse_mode="Markdown",
    )
    return NEW_NAME


async def new_patient_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name or len(name) > 100:
        await update.message.reply_text("❌ Invalid name (max 100 chars). Try again:")
        return NEW_NAME
    context.user_data["new_patient"] = {"name": name}
    await update.message.reply_text(
        f"👤 Patient: *{name}*\n\nStep 2/4 - Choose the service:",
        parse_mode="Markdown",
        reply_markup=service_inline_kb(),
    )
    return NEW_TYPE


async def new_patient_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    service = q.data.replace("svc_", "")
    context.user_data.setdefault("new_patient", {})["service"] = service
    await q.edit_message_text(
        f"🦷 Service: *{SERVICE_LABELS[service]}*\n\n"
        "Step 3/4 - Enter the tooth color (e.g. A2, B1):",
        parse_mode="Markdown",
    )
    return NEW_COLOR


async def new_patient_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    color = update.message.text.strip()
    if not color or len(color) > 50:
        await update.message.reply_text("❌ Invalid color. Try again:")
        return NEW_COLOR
    context.user_data["new_patient"]["color"] = color
    await update.message.reply_text(
        f"🎨 Color: *{color}*\n\nStep 4/4 - Enter the number of units:",
        parse_mode="Markdown",
    )
    return NEW_COUNT


async def new_patient_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    try:
        count = int(txt)
        if count <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid count. Send a positive integer:")
        return NEW_COUNT

    data = context.user_data.get("new_patient", {})
    if not all(k in data for k in ("name", "service", "color")):
        await update.message.reply_text(
            "⚠️ Something went wrong. Please start over.",
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END

    unit = db_get_price(data["service"])
    total = unit * count
    db_add_patient(data["name"], data["service"], data["color"], count, total)

    summary = (
        "✅ *Patient Saved*\n\n"
        f"👤 Name:        {data['name']}\n"
        f"🦷 Service:     {SERVICE_LABELS[data['service']]}\n"
        f"🎨 Tooth Color: {data['color']}\n"
        f"🔢 Count:       {count}\n"
        f"💵 Unit Price:  {fmt_price(unit)}\n"
        f"💰 *Total:      {fmt_price(total)}*"
    )
    await update.message.reply_text(
        summary, parse_mode="Markdown", reply_markup=main_menu_kb()
    )
    context.user_data.pop("new_patient", None)
    return ConversationHandler.END


async def specialist_placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🩺 *Specialist Doctor*\n\n🚧 Coming soon.\n"
        "This section will analyze 2D dental scans using AI.",
        parse_mode="Markdown",
    )


async def ai_placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *AI Assistant*\n\n🚧 Coming soon.\n"
        "This section will connect to DeepSeek for dental Q&A.",
        parse_mode="Markdown",
    )


async def escape_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = update.message.text
    if text == "👥 Patients":
        await show_patients(update, context)
    elif text == "💰 Prices":
        await show_prices(update, context)
    elif text == "🩺 Specialist Doctor":
        await specialist_placeholder(update, context)
    elif text == "🤖 AI":
        await ai_placeholder(update, context)
    elif text == "🆕 New Patient":
        return await new_patient_start(update, context)
    return ConversationHandler.END


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤔 I didn't understand that. Please use the menu.",
        reply_markup=main_menu_kb(),
    )


def build_application() -> Application:
    init_db()
    ptb = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^💰 Prices$"), show_prices),
            MessageHandler(filters.Regex("^🆕 New Patient$"), new_patient_start),
            MessageHandler(filters.Regex("^👥 Patients$"), show_patients),
            MessageHandler(
                filters.Regex("^🩺 Specialist Doctor$"), specialist_placeholder
            ),
            MessageHandler(filters.Regex("^🤖 AI$"), ai_placeholder),
            CallbackQueryHandler(price_service_chosen, pattern="^price_"),
        ],
        states={
            WAITING_PRICE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTONS_REGEX),
                    price_input,
                ),
            ],
            NEW_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTONS_REGEX),
                    new_patient_name,
                ),
            ],
            NEW_TYPE: [
                CallbackQueryHandler(new_patient_type, pattern="^svc_"),
            ],
            NEW_COLOR: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTONS_REGEX),
                    new_patient_color,
                ),
            ],
            NEW_COUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTONS_REGEX),
                    new_patient_count,
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start", cmd_cancel),
            MessageHandler(filters.Regex(MENU_BUTTONS_REGEX), escape_to_menu),
        ],
        per_message=False,
    )

    ptb.add_handler(CommandHandler("start", cmd_start))
    ptb.add_handler(CommandHandler("help", cmd_help))
    ptb.add_handler(CommandHandler("cancel", cmd_cancel))
    ptb.add_handler(conv)
    ptb.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    return ptb


app = Flask(__name__)

_ptb_app = None
_loop = None
_webhook_set = False


def _ensure_app():
    global _ptb_app, _loop
    if _ptb_app is not None:
        return _ptb_app, _loop

    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _ptb_app = build_application()
    _loop.run_until_complete(_ptb_app.initialize())
    logger.info("PTB Application initialized.")
    return _ptb_app, _loop


def _maybe_set_webhook():
    global _webhook_set
    if _webhook_set:
        return

    base = WEBHOOK_URL
    if not base and has_request_context():
        base = request.host_url.rstrip("/")

    if not base:
        return

    ptb_app, loop = _ensure_app()
    try:
        loop.run_until_complete(
            ptb_app.bot.set_webhook(
                url=f"{base}/webhook",
                secret_token=WEBHOOK_SECRET or None,
                drop_pending_updates=True,
            )
        )
        _webhook_set = True
        logger.info("Webhook set to %s/webhook", base)
    except Exception:
        logger.exception("Failed to set webhook")


@app.route("/", methods=["GET"])
def index():
    _maybe_set_webhook()
    return "Dental Bot is running."


@app.route("/health", methods=["GET"])
def health():
    _maybe_set_webhook()
    return "ok"


@app.route("/set-webhook", methods=["GET"])
def set_webhook_route():
    _maybe_set_webhook()
    return "webhook configured"


@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if incoming != WEBHOOK_SECRET:
            abort(403)

    ptb_app, loop = _ensure_app()
    update = Update.de_json(request.get_json(force=True), ptb_app.bot)
    if update is None:
        return "no update", 200

    try:
        loop.run_until_complete(ptb_app.process_update(update))
    except Exception:
        logger.exception("Error processing update")
        return "error", 500

    return "ok", 200


if __name__ == "__main__":
    init_db()
    if WEBHOOK_URL:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
    else:
        application = build_application()
        logger.info("Running in polling mode (local dev).")
        application.run_polling()
