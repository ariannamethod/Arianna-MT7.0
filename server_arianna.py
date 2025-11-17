"""
Arianna Telegram Server - Entry Point

This is the main entry point for the Telegram interface.
All Telegram-specific logic has been moved to interfaces/telegram_bot.py.
This file handles:
- Webhook setup
- Snapshot service
- Health check routes
"""

import os
import asyncio

from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from interfaces.telegram_bot import TelegramInterface
from utils.tasks import create_task, cancel_tracked
from utils.state_snapshot import run_snapshot_service
from utils.repo_monitor import check_repository_changes
from utils.logging import get_logger


logger = get_logger(__name__)


# Health check routes
async def healthz(request):
    """Health check endpoint."""
    return web.Response(text="ok")


async def status(request):
    """Status endpoint."""
    return web.Response(text="running")


async def main():
    """Main entry point - setup webhook and start services."""
    # Get bot token
    bot_token = os.getenv("TELEGRAM_TOKEN")
    if not bot_token:
        raise ValueError("TELEGRAM_TOKEN environment variable is required")

    # Create Telegram interface
    # Set use_core_engine=True to use new AriannaCoreEngine
    # Set use_core_engine=False to use legacy AriannaEngine (default for now)
    use_core = os.getenv("USE_CORE_ENGINE", "false").lower() == "true"
    interface = TelegramInterface(token=bot_token, use_core_engine=use_core)

    logger.info(
        "Starting Arianna MT7.0 Telegram interface (core_engine=%s)",
        use_core
    )

    # Initialize interface (setup bot commands, get bot info, etc.)
    await interface.on_startup()

    # Check repository for config changes and reindex if needed
    logger.info("Checking repository for config changes...")
    try:
        await check_repository_changes(vector_store=interface.vector_store)
    except Exception as e:
        logger.error("Repository check failed: %s", e, exc_info=True)

    # Setup legacy assistant if needed
    init_failed = False
    if not use_core:
        init_failed = not await interface.setup_legacy_assistant()

    # Start background services
    create_task(run_snapshot_service(), name="snapshot_service", track=True)

    # Setup webhook
    try:
        app = web.Application()
        path = f"/webhook/{bot_token}"

        if init_failed:
            # If assistant init failed, return 500 on webhook
            async def failed(request):
                return web.Response(status=500, text="Initialization failed")
            app.router.add_route("*", path, failed)
            app.router.add_route("*", "/webhook", failed)
        else:
            # Normal webhook handler
            handler = SimpleRequestHandler(
                dispatcher=interface.dp,
                bot=interface.bot
            )
            handler.register(app, path=path)
            handler.register(app, path="/webhook")
            setup_application(app, interface.dp)

        # Register health check routes
        app.router.add_get("/healthz", healthz)
        app.router.add_get("/status", status)

        # Start web server
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv("PORT", 8000))
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()

        logger.info("Arianna webhook started on port %d", port)
        logger.info("Webhook path: %s", path)
        logger.info("Health checks: /healthz, /status")

        # Wait forever
        await asyncio.Event().wait()
    finally:
        # Cancel all tracked tasks
        await cancel_tracked()


if __name__ == "__main__":
    asyncio.run(main())
