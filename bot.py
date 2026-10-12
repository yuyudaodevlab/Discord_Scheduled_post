import asyncio
import logging

import discord
from discord import app_commands

import config
from commands import install
from scheduler import Scheduler
from storage import Storage

log = logging.getLogger(__name__)


class ScheduleBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.store = Storage(config.DATA_PATH)
        self.tree = app_commands.CommandTree(self)
        self.scheduler = Scheduler(self, self.store, config.MISSED_GRACE_SECONDS)
        install(self.tree, self.scheduler)

    async def setup_hook(self):
        await asyncio.to_thread(self.store.load)
        if config.TEST_GUILD_ID:
            guild = discord.Object(id=config.TEST_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.scheduler.loop.start()

    async def on_ready(self):
        log.info("Bot起動・接続完了: user_id=%s", self.user.id)

    async def close(self):
        task = self.scheduler.loop.get_task()
        self.scheduler.loop.cancel()
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("監視タスク終了エラー")
        await super().close()
        self.store.close()
        log.info("Bot停止")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.TOKEN or config.TOKEN == "your_bot_token_here":
        raise SystemExit(".env の DISCORD_BOT_TOKEN にBot Tokenを設定してください。")
    bot = ScheduleBot()
    bot.run(config.TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
