"""
telegram_server.py — Telegram bot handlers and application setup.
"""

import os
import logging
import asyncio
import time
import tempfile
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
)

from graph import build_graph
from core import invoke_graph, extract_response, stream_graph
from utils.formatting import format_for_telegram
from utils.media import speech_to_text, text_to_speech

logger = logging.getLogger("denver.telegram")

# ─── Globals ──────────────────────────────────────────────────────────────────
agent_app = None
ALLOWED_TELEGRAM_USER_ID = None
TTS_CONFIG = {}

# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def send_typing_continuous(chat_id: int, bot, stop_event: asyncio.Event):
    """Send ChatAction.TYPING every 5 seconds until stop_event is set."""
    try:
        while not stop_event.is_set():
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass

async def check_auth(update: Update) -> bool:
    """Check if the user is authorized to interact with the bot."""
    user_id = update.effective_user.id
    if ALLOWED_TELEGRAM_USER_ID and str(user_id) != str(ALLOWED_TELEGRAM_USER_ID):
        logger.warning(f"Intento de acceso no autorizado: user_id={user_id}")
        await update.message.reply_text("⛔ Usuario no autorizado.")
        return False
    return True

# ═══════════════════════════════════════════════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not await check_auth(update):
        return
    await update.message.reply_text(
        f"¡Hola! Soy Denver 🤖, tu asistente personal.\n"
        f"Podés enviarme texto o notas de voz. Usá /status para ver el estado del sistema."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages — invoke graph and reply with text (+ photo if camera)."""
    if not await check_auth(update):
        return
    chat_id = update.effective_chat.id
    texto = update.message.text

    stop_event = asyncio.Event()
    typing_task = asyncio.create_task(
        send_typing_continuous(chat_id, context.bot, stop_event)
    )

    try:
        msg = await update.message.reply_text("...")
        accumulated = ""
        last_edit_time = time.time()
        result = None
        
        async for item_type, data in stream_graph(agent_app, texto, chat_id, is_voice=False, interface="telegram"):
            if item_type == "chunk":
                accumulated += data
                if time.time() - last_edit_time > 1.0:
                    try:
                        await msg.edit_text(accumulated)
                        last_edit_time = time.time()
                    except Exception:
                        pass
            elif item_type == "result":
                result = data

        respuesta = format_for_telegram(extract_response(result or {}))
        try:
            await msg.edit_text(respuesta, parse_mode="HTML")
        except Exception:
            await msg.edit_text(respuesta)

        # Send camera photo if present
        if result:
            image_binary = result.get("image_binary")
            if image_binary:
                await update.message.reply_photo(image_binary)

    except Exception as e:
        logger.error(f"[handle_message] chat={chat_id} | ERROR: {e}")
        await update.message.reply_text(
            f"⚠️ Error procesando tu mensaje: {str(e)[:150]}"
        )
    finally:
        stop_event.set()
        typing_task.cancel()

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages — STT → graph → TTS → reply voice (+ photo if camera)."""
    if not await check_auth(update):
        return
    chat_id = update.effective_chat.id
    file_path = None

    try:
        # Download voice file
        voice_file = await update.message.voice.get_file()
        file_path = os.path.join(tempfile.gettempdir(), f"voice_{voice_file.file_id}.ogg")
        await voice_file.download_to_drive(file_path)

        # STT
        texto = speech_to_text(file_path)
        if not texto:
            await update.message.reply_text("❌ No pude entender el audio.")
            return

        # Typing indicator
        stop_event = asyncio.Event()
        typing_task = asyncio.create_task(
            send_typing_continuous(chat_id, context.bot, stop_event)
        )

        try:
            # Graph invocation
            result = await invoke_graph(agent_app, texto, chat_id, is_voice=True, interface="telegram")
            respuesta = extract_response(result)

            # TTS
            audio = await text_to_speech(respuesta, TTS_CONFIG)
            if audio:
                await update.message.reply_voice(audio)
            else:
                logger.warning("[TTS] fallo — fallback a texto")
                await update.message.reply_text(format_for_telegram(respuesta), parse_mode="HTML")

            # Camera photo if present
            image_binary = result.get("image_binary")
            if image_binary:
                await update.message.reply_photo(image_binary)

        finally:
            stop_event.set()
            typing_task.cancel()

    except Exception as e:
        logger.error(f"[handle_voice] chat={chat_id} | ERROR: {e}")
        await update.message.reply_text(
            f"⚠️ Error procesando tu audio: {str(e)[:150]}"
        )
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photos/images sent by the user — inject into graph as image_binary."""
    if not await check_auth(update):
        return
    chat_id = update.effective_chat.id

    stop_event = asyncio.Event()
    typing_task = asyncio.create_task(
        send_typing_continuous(chat_id, context.bot, stop_event)
    )

    try:
        # Get the highest-resolution photo available
        if update.message.photo:
            photo = update.message.photo[-1]  # largest size
            tg_file = await photo.get_file()
        elif update.message.document and update.message.document.mime_type.startswith("image/"):
            tg_file = await update.message.document.get_file()
        else:
            await update.message.reply_text("❌ No pude leer la imagen enviada.")
            return

        # Download image bytes
        image_bytes = await tg_file.download_as_bytearray()
        image_bytes = bytes(image_bytes)

        # Inject image into the tools side channel so analizar_imagen can access it
        import tools as tools_module
        tools_module._captured_image = image_bytes

        # Use caption as the user message, or fallback to a default prompt
        caption = (update.message.caption or "").strip()
        texto = caption if caption else "Analizá esta imagen y decime qué ves."

        logger.info(f"[handle_photo] chat={chat_id} | caption={texto!r} | {len(image_bytes)} bytes")

        msg = await update.message.reply_text("...")
        accumulated = ""
        last_edit_time = time.time()
        result = None

        async for item_type, data in stream_graph(
            agent_app, texto, chat_id,
            is_voice=False, interface="telegram",
            image_binary=image_bytes,
        ):
            if item_type == "chunk":
                accumulated += data
                if time.time() - last_edit_time > 1.0:
                    try:
                        await msg.edit_text(accumulated)
                        last_edit_time = time.time()
                    except Exception:
                        pass
            elif item_type == "result":
                result = data

        respuesta = format_for_telegram(extract_response(result or {}))
        
        try:
            await msg.edit_text(respuesta, parse_mode="HTML")
        except Exception:
            await msg.edit_text(respuesta)

        # Send any camera photo returned by the graph (shouldn't happen here, but handle anyway)
        if result:
            out_image = result.get("image_binary")
            if out_image and out_image != image_bytes:
                await update.message.reply_photo(out_image)


    except Exception as e:
        logger.error(f"[handle_photo] chat={chat_id} | ERROR: {e}")
        await update.message.reply_text(
            f"⚠️ Error procesando la imagen: {str(e)[:150]}"
        )
    finally:
        stop_event.set()
        typing_task.cancel()

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Real-time diagnostic — does NOT go through the LLM graph."""
    from db.database import async_session as db_session
    from db.models import Monitor, Memoria
    from sqlalchemy import func, select
    import aiohttp

    chat_id = update.effective_chat.id
    if not await check_auth(update):
        return
    timeout = int(os.getenv("STATUS_PING_TIMEOUT", "5"))

    async def check_ha():
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.get(
                    f"{os.getenv('HOME_ASSISTANT_URL', '').rstrip('/')}/api/",
                    headers={"Authorization": f"Bearer {os.getenv('HOME_ASSISTANT_TOKEN')}"},
                    timeout=aiohttp.ClientTimeout(total=timeout),
                )
                return "✅ conectado" if resp.status == 200 else f"❌ HTTP {resp.status}"
        except Exception:
            return "❌ no disponible"

    async def check_tts():
        try:
            url = TTS_CONFIG.get('url', 'http://localhost:5050/v1')
            async with aiohttp.ClientSession() as session:
                resp = await session.get(
                    f"{url}/models",
                    timeout=aiohttp.ClientTimeout(total=timeout),
                )
                return "✅ conectado" if resp.status == 200 else f"❌ HTTP {resp.status}"
        except Exception:
            return "❌ no disponible"

    async def check_llm():
        try:
            url = os.getenv("LLM_URL", "http://localhost:1234/v1")
            async with aiohttp.ClientSession() as session:
                resp = await session.get(
                    f"{url.rstrip('/')}/models",
                    timeout=aiohttp.ClientTimeout(total=timeout),
                )
                return "✅ conectado" if resp.status == 200 else f"❌ HTTP {resp.status}"
        except Exception:
            return "❌ no disponible"

    async def check_google():
        try:
            if os.path.exists("token.json"):
                from google.oauth2.credentials import Credentials
                creds = Credentials.from_authorized_user_file("token.json")
                if creds.valid:
                    return "✅ token válido"
                elif creds.expired:
                    return "⚠️ token expirado (se refrescará)"
                return "❌ token inválido"
            return "❌ token.json no encontrado"
        except Exception:
            return "❌ error leyendo token"

    async def check_db():
        try:
            async with db_session() as session:
                mon_count = await session.scalar(select(func.count(Monitor.id)))
                mem_count = await session.scalar(select(func.count(Memoria.id)))
                return f"{mon_count} monitores | {mem_count} memorias"
        except Exception:
            return "❌ error"

    # Run checks
    ha_status, llm_status, tts_status, google_status, db_status = await asyncio.gather(
        check_ha(), check_llm(), check_tts(), check_google(), check_db()
    )

    llm_model = os.getenv("LLM_MODEL", "unknown")
    scheduler_lines = ""
    try:
        from scheduler.monitor_runner import _last_run
        for job_name, last_time in _last_run.items():
            if last_time:
                elapsed = (asyncio.get_event_loop().time() - last_time.timestamp())
                ago = f"hace {int(elapsed)} seg" if elapsed < 60 else f"hace {int(elapsed / 60)} min"
            else:
                ago = "nunca"
            scheduler_lines += f"\n    • {job_name}: {ago}"
    except Exception:
        scheduler_lines = "\n    ⚠️ no disponible"

    mem_global = "✅ activa" if os.getenv("MEMORIA_PERSISTENTE", "true").lower() == "true" else "❌ desactivada"

    status_msg = (
        "🤖 <b>Denver — Estado del sistema</b>\n\n"
        f"🏠 Home Assistant: {ha_status}\n"
        f"🧠 LLM: {llm_status} ({llm_model})\n"
        f"🔊 TTS: {tts_status}\n"
        f"📧 Google APIs: {google_status}\n"
        f"⏱️ Scheduler:{scheduler_lines}\n"
        f"🗄️ DB: {db_status}\n"
        f"💾 Memoria: {mem_global}"
    )

    logger.info(f"[Status] /status solicitado por {chat_id}")
    await update.message.reply_text(status_msg, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════════════════
# SERVER SETUP
# ═══════════════════════════════════════════════════════════════════════════════

async def start_telegram():
    """Build and start the Telegram application."""
    global agent_app, ALLOWED_TELEGRAM_USER_ID, TTS_CONFIG
    
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN logic check failed.")
        return

    raw_id = os.getenv("ALLOWED_TELEGRAM_USER_ID")
    ALLOWED_TELEGRAM_USER_ID = raw_id.split('#')[0].strip() if raw_id else None
    
    TTS_CONFIG = {
        "url": os.getenv("TTS_URL", "http://localhost:5050/v1"),
        "key": os.getenv("LOCAL_TTS_KEY", ""),
        "model": os.getenv("TTS_MODEL", "tts-1"),
        "voice": os.getenv("TTS_VOICE", "es-AR-TomasNeural"),
        "speed": os.getenv("TTS_SPEED", "1.3"),
    }

    # Build graph if not already built
    if agent_app is None:
        agent_app = await build_graph()

    # Build Telegram app
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))

    # Start scheduler
    logger.info("Iniciando scheduler proactivo...")
    from scheduler.monitor_runner import start_scheduler
    await start_scheduler(app.bot)

    # Initialize and start polling
    logger.info("🤖 Denver Telegram Bot está listo.")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    return app
