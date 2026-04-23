import os
import asyncio
import random
import discord

from config import (
    GUILD_ID,
    GIVEAWAYS_FILE,
    TICKETS_FILE,
    VERIFY_FILE,
    GIVEAWAY_PUBLIC_CHANNEL_ID,
    TICKET_CATEGORY_ID,
    TICKET_STAFF_ROLE_ID,
    VERIFIED_ROLE_ID,
    UNVERIFIED_ROLE_ID,
    MAX_VERIFY_TRIES,
    DATA_DIR,
)
from storage import load_json, save_json
from utils import (
    now_utc,
    dt_to_iso,
    ts_full,
    ts_relative,
    sanitize_channel_name,
    is_giveaway_staff,
    is_ticket_staff,
    is_verified,
    random_code,
    captcha_discord_file,
)

class GiveawayCreateModal(discord.ui.Modal, title="Créer un giveaway"):
    prize = discord.ui.TextInput(label="Lot", placeholder="Exemple : Nitro 1 mois", max_length=100)
    duration_minutes = discord.ui.TextInput(label="Durée en minutes", placeholder="Exemple : 60", max_length=10)
    winners_count = discord.ui.TextInput(label="Nombre de gagnants", placeholder="Exemple : 1", max_length=3)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)
        if not is_giveaway_staff(interaction.user):
            return await interaction.response.send_message("Seuls les modérateurs peuvent créer un giveaway.", ephemeral=True)

        try:
            duration = int(str(self.duration_minutes))
            winners = int(str(self.winners_count))
        except ValueError:
            return await interaction.response.send_message("Valeurs invalides.", ephemeral=True)

        if duration <= 0 or winners <= 0:
            return await interaction.response.send_message("Les valeurs doivent être supérieures à 0.", ephemeral=True)

        public_channel = interaction.guild.get_channel(GIVEAWAY_PUBLIC_CHANNEL_ID)
        if not isinstance(public_channel, discord.TextChannel):
            return await interaction.response.send_message("Salon giveaways introuvable.", ephemeral=True)

        end_at = now_utc() + asyncio.timedelta(minutes=duration)  # patched below
