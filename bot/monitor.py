"""Background monitoring loop for Magnit promotions."""

import asyncio
import logging
from typing import Dict, List, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile, InputMediaPhoto

from .config import POLL_INTERVAL
from .database import Database
from .magnit_api import MagnitClient

logger = logging.getLogger(__name__)

# Max notifications sent per store per poll (protects users from
# message floods on the very first check)
MAX_NOTIFY_PER_STORE = 5


class Monitor:
    """Polls Magnit API for promotions and sends new ones to subscribed users."""

    def __init__(self, bot: Bot, db: Database, magnit_client: MagnitClient) -> None:
        self.bot = bot
        self.db = db
        self.magnit_client = magnit_client
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start the polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Monitor started")

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Monitor stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._check_all_stores()
            except Exception:
                logger.exception("Error in poll loop iteration")
            await asyncio.sleep(POLL_INTERVAL)

    async def _check_all_stores(self) -> None:
        """Check promotions for all stores and notify subscribed users."""
        stores = await self.db.get_all_stores()
        if not stores:
            return

        # Group stores by code to avoid duplicate API calls
        store_codes: Dict[str, List[dict]] = {}
        for store in stores:
            code = store["store_code"]
            store_codes.setdefault(code, []).append(store)

        for code, store_list in store_codes.items():
            try:
                promotions = await self.magnit_client.get_promotions_for_store(code)
            except Exception:
                logger.exception("Failed to fetch promotions for store %s", code)
                continue

            if not promotions:
                continue

            # Notify users subscribed to this store
            users = await self.db.get_users_for_store(code)
            sent_per_user: Dict[int, int] = {}
            for promo in promotions:
                discount = promo.get("discount_percent", 0)
                for user in users:
                    min_disc = user.get("min_discount", 10)
                    if discount < min_disc:
                        continue

                    user_id = user["user_id"]
                    if sent_per_user.get(user_id, 0) >= MAX_NOTIFY_PER_STORE:
                        continue

                    product_id = promo.get("product_id", "")
                    is_sent = await self.db.is_sent(user_id, product_id, code)
                    if is_sent:
                        continue

                    await self._send_promotion(user, promo)
                    await self.db.mark_sent(user_id, product_id, code)
                    sent_per_user[user_id] = sent_per_user.get(user_id, 0) + 1

    async def _send_promotion(self, user: dict, promo: dict) -> None:
        """Send a promotion notification to a user."""
        user_id = user["user_id"]
        name = promo.get("name", "Товар")
        discount = promo.get("discount_percent", 0)
        old_price = promo.get("old_price")
        new_price = promo.get("new_price")
        address = promo.get("store_address") or user.get("store_address", "")
        image_url = promo.get("image_url")
        category = promo.get("category")

        lines = [
            "🔥 <b>Новая акция в Магните!</b>",
            "",
            f"🛍 <b>{name}</b>",
            f"🏷 Скидка: <b>{discount}%</b>",
        ]

        if old_price and new_price:
            lines.append(f"💰 {old_price:.2f} ₽ → <b>{new_price:.2f} ₽</b>")
        elif new_price:
            lines.append(f"💰 Цена: <b>{new_price:.2f} ₽</b>")

        if category:
            lines.append(f"📂 Категория: {category}")
        if address:
            lines.append(f"📍 Магазин: {address}")

        caption = "\n".join(lines)

        try:
            if image_url:
                try:
                    await self.bot.send_photo(
                        chat_id=user_id,
                        photo=image_url,
                        caption=caption,
                        parse_mode="HTML",
                    )
                    return
                except TelegramBadRequest as exc:
                    # Telegram sometimes cannot fetch the image URL itself
                    # ("failed to get HTTP URL content") - send text instead
                    logger.warning(
                        "Photo failed for user %s (%s), sending text only",
                        user_id,
                        exc,
                    )
            await self.bot.send_message(
                chat_id=user_id,
                text=caption,
                parse_mode="HTML",
            )
        except TelegramForbiddenError:
            logger.warning("User %s blocked the bot; skipping", user_id)
        except TelegramBadRequest as exc:
            logger.warning("Cannot deliver to user %s: %s", user_id, exc)
        except Exception:
            logger.exception("Failed to send promotion to user %s", user_id)