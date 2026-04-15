"""
run.py — Unified entry point for Denver ecosystem.
Usage: python run.py [--telegram] [--web] [--voice] [--all]
"""

import asyncio
import logging
import os
import sys
import signal
from dotenv import load_dotenv
import uvicorn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("denver.run")

async def run_telegram(stop_event: asyncio.Event):
    from telegram_server import start_telegram
    app = await start_telegram()
    if not app:
        return
    
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Cerrando Telegram Server...")
        if app.updater and app.updater.running:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
        
        from telegram_server import agent_app
        if agent_app and hasattr(agent_app.checkpointer, "conn"):
            await agent_app.checkpointer.conn.close()

async def run_web(stop_event: asyncio.Event):
    from web_server import app as web_app
    port = int(os.getenv("WEB_SERVER_PORT", "8002"))
    config = uvicorn.Config(web_app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    task = asyncio.create_task(server.serve())

    def on_task_done(t):
        try:
            t.result()
        except Exception as e:
            logger.error(f"Web server task crashed: {e}", exc_info=True)

    task.add_done_callback(on_task_done)

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Cerrando Web Server...")
        server.should_exit = True
        await task

async def run_voice(stop_event: asyncio.Event):
    from voice_server import app as voice_app
    port = int(os.getenv("VOICE_SERVER_PORT", "8001"))
    config = uvicorn.Config(
        voice_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        timeout_keep_alive=120,
        h11_max_incomplete_event_size=2097152,
    )
    server = uvicorn.Server(config)
    
    task = asyncio.create_task(server.serve())
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Cerrando Voice Server...")
        server.should_exit = True
        await task

async def main():
    load_dotenv()
    
    args = sys.argv[1:]
    run_all = "--all" in args or not args
    
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    
    def _signal_handler():
        logger.info("Shutdown signal received...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass # Windows
            
    tasks = []
    
    if run_all or "--telegram" in args:
        logger.info("Starting Telegram Server...")
        tasks.append(asyncio.create_task(run_telegram(stop_event)))
        
    if run_all or "--web" in args:
        logger.info("Starting Web Server...")
        tasks.append(asyncio.create_task(run_web(stop_event)))
        
    if run_all or "--voice" in args:
        logger.info("Starting Voice Server...")
        tasks.append(asyncio.create_task(run_voice(stop_event)))

    if not tasks:
        logger.error("No servers selected to run.")
        return

    try:
        # Wait until stop_event is set in a polling loop to cleanly catch KeyboardInterrupt on Windows
        while not stop_event.is_set():
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt (Ctrl+C) recibido.")
        stop_event.set()
    except asyncio.CancelledError:
        stop_event.set()
    
    logger.info("Esperando apagado limpio de servidores (esto puede tomar unos segundos)...")
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Todos los servidores detenidos exitosamente.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
