import threading
import discord
from discord.ext import commands

from config import TOKEN, PORT
from storage import init_storage
from bot_commands import setup_commands
from bot_events import setup_events
from bot_views import (
    GiveawayStaffPanelView,
    GiveawayJoinView,
    GiveawayEndedView,
    TicketOpenView,
    TicketManageView,
    VerifyPanelView,
)
from webapp import create_app


class XeraxBot(commands.Bot):
    async def setup_hook(self):
        # Les vues persistantes doivent être ajoutées ici
        self.add_view(GiveawayStaffPanelView())
        self.add_view(GiveawayJoinView())
        self.add_view(GiveawayEndedView())
        self.add_view(TicketOpenView())
        self.add_view(TicketManageView())
        self.add_view(VerifyPanelView())


def make_bot():
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True
    intents.messages = True
    intents.message_content = True

    bot = XeraxBot(command_prefix="!", intents=intents)

    setup_commands(bot)
    setup_events(bot)

    return bot


def run_bot(bot):
    bot.run(TOKEN)


if __name__ == "__main__":
    init_storage()

    bot = make_bot()
    app = create_app(bot)

    bot_thread = threading.Thread(target=run_bot, args=(bot,), daemon=True)
    bot_thread.start()

    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)
