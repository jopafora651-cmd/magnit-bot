"""Main entry point for the Magnit promo bot."""

import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject
from dotenv import load_dotenv

from . import hf_storage
from .config import BOT_TOKEN, MAGNIT_API_BASE
from .database import Database
from .magnit_api import MagnitClient
from .monitor import Monitor
from .store_search import StoreSearcher
from .handlers import router

# Load environment
load_dotenv()

# Configure logging: always to a file (the bot may run with pythonw, which
# has no console), plus console output when one is attached.
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
LOG_FILE = os.path.join(LOG_DIR, "bot.log")
os.makedirs(LOG_DIR, exist_ok=True)

_handlers: list[logging.Handler] = [
    RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
]
if sys.stderr is not None:  # pythonw.exe has no console
    _handlers.append(logging.StreamHandler())
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger(__name__)


def prevent_system_sleep() -> None:
    """On Windows, keep the machine awake while the bot is running.

    Uses SetThreadExecutionState: no permanent power-settings changes; the
    previous behaviour returns as soon as the bot exits. Does not override
    an explicit user action (sleep button / lid close).
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        logger.info("System sleep prevention enabled (bot keeps laptop awake)")
    except Exception:  # noqa: BLE001
        logger.warning("Could not enable sleep prevention", exc_info=True)


async def start_health_server() -> None:
    """Serve a tiny HTTP endpoint.

    Required by Hugging Face Spaces (the container must listen on a port);
    also used as the keep-alive ping target by uptime monitors.
    """
    port = int(os.getenv("PORT", "7860"))

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health server listening on 0.0.0.0:%s", port)


async def persistence_loop() -> None:
    """Periodically back up the database to the HF dataset repo."""
    if not hf_storage.is_enabled():
        logger.info("HF persistence disabled (no HF_TOKEN / not running on a Space)")
        return

    last_hash: str | None = None
    # Push the initial state right after startup
    current = await asyncio.to_thread(hf_storage.db_hash)
    if current:
        ok = await asyncio.to_thread(hf_storage.push_db)
        if ok:
            last_hash = current

    while True:
        await asyncio.sleep(hf_storage.PUSH_INTERVAL)
        try:
            current = await asyncio.to_thread(hf_storage.db_hash)
            if current and current != last_hash:
                ok = await asyncio.to_thread(hf_storage.push_db)
                if ok:
                    last_hash = current
        except Exception:  # noqa: BLE001
            logger.exception("Persistence loop iteration failed")


class DependenciesMiddleware:
    """Injects shared dependencies into every handler call."""

    def __init__(
        self,
        db: Database,
        magnit_client: MagnitClient,
        store_searcher: StoreSearcher,
        session: aiohttp.ClientSession,
    ) -> None:
        self.db = db
        self.magnit_client = magnit_client
        self.store_searcher = store_searcher
        self.session = session

    async def __call__(self, handler, event: TelegramObject, data: dict):
        data["db"] = self.db
        data["magnit_client"] = self.magnit_client
        data["store_searcher"] = self.store_searcher
        data["session"] = self.session
        return await handler(event, data)


async def main() -> None:
    """Run the bot."""
    if not BOT_TOKEN:
        raise ValueError(
            "BOT_TOKEN not set. Create a .env file in the magnit-bot directory with:\n"
            "BOT_TOKEN=your_bot_token_here"
        )

    # Bot
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))

    # Keep the laptop awake while the bot is running (Windows only, no-op elsewhere)
    prevent_system_sleep()

    # Restore the database backup when running on Hugging Face
    if hf_storage.is_enabled():
        await asyncio.to_thread(hf_storage.pull_db)

    # Database
    db = Database()
    await db.init()

    # HTTP session for Magnit API
    session = aiohttp.ClientSession(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        }
    )

    # Magnit API clients
    magnit_client = MagnitClient(session=session, base_url=MAGNIT_API_BASE)
    store_searcher = StoreSearcher(session=session, base_url=MAGNIT_API_BASE)

    # Monitor
    monitor = Monitor(bot, db, magnit_client)

    # Dispatcher
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Dependency injection middleware
    deps_middleware = DependenciesMiddleware(db, magnit_client, store_searcher, session)
    dp.message.middleware(deps_middleware)
    dp.callback_query.middleware(deps_middleware)

    # Start monitoring
    await monitor.start()

    # HTTP health server (required on HF Spaces) + DB persistence loop
    await start_health_server()
    persistence_task = asyncio.create_task(persistence_loop())

    # Start polling
    try:
        await dp.start_polling(bot)
    finally:
        await monitor.stop()
        persistence_task.cancel()
        try:
            await persistence_task
        except asyncio.CancelledError:
            pass
        # Final DB backup on shutdown (deploy/restart/spin-down) so the
        # latest changes are not lost between periodic backups
        if hf_storage.is_enabled():
            await asyncio.to_thread(hf_storage.push_db)
        await session.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())