import discord
from discord import app_commands

from storage import load_json, save_json
from config import GUILD_ID, BLACKLIST_FILE
from utils import now_utc


DELETE_MESSAGE_CHOICES = [
    app_commands.Choice(name="Don't Delete Any", value=0),
    app_commands.Choice(name="Previous Hour", value=3600),
    app_commands.Choice(name="Previous 6 Hours", value=21600),
    app_commands.Choice(name="Previous 12 Hours", value=43200),
    app_commands.Choice(name="Previous 24 Hours", value=86400),
    app_commands.Choice(name="Previous 3 Days", value=259200),
    app_commands.Choice(name="Previous 7 Days", value=604800),
]


def _add_blacklist(user_id: int):
    data = load_json(BLACKLIST_FILE, {"banned_ids": []})
    if user_id not in data["banned_ids"]:
        data["banned_ids"].append(user_id)
        save_json(BLACKLIST_FILE, data)


def _remove_blacklist(user_id: int):
    data = load_json(BLACKLIST_FILE, {"banned_ids": []})
    if user_id in data["banned_ids"]:
        data["banned_ids"].remove(user_id)
        save_json(BLACKLIST_FILE, data)


def setup_commands(bot):
    @bot.tree.command(name="ban", description="Bannir un membre")
    @app_commands.describe(user="Membre à bannir", delete_messages="Messages à supprimer", reason="Raison")
    @app_commands.choices(delete_messages=DELETE_MESSAGE_CHOICES)
    async def slash_ban(
        interaction: discord.Interaction,
        user: discord.Member,
        delete_messages: app_commands.Choice[int],
        reason: str,
    ):
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message("Serveur invalide.", ephemeral=True)

        if not interaction.user.guild_permissions.ban_members:
            return await interaction.response.send_message("❌ Pas de permission.", ephemeral=True)

        try:
            await interaction.guild.ban(
                user,
                delete_message_seconds=delete_messages.value,
                reason=f"{reason} | par {interaction.user}",
            )
            _add_blacklist(user.id)
            await interaction.response.send_message(f"✅ {user.mention} a été banni.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)

    @bot.tree.command(name="banid", description="Bannir un utilisateur par ID")
    @app_commands.describe(user_id="ID utilisateur", delete_messages="Messages à supprimer", reason="Raison")
    @app_commands.choices(delete_messages=DELETE_MESSAGE_CHOICES)
    async def slash_banid(
        interaction: discord.Interaction,
        user_id: str,
        delete_messages: app_commands.Choice[int],
        reason: str,
    ):
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message("Serveur invalide.", ephemeral=True)

        if not interaction.user.guild_permissions.ban_members:
            return await interaction.response.send_message("❌ Pas de permission.", ephemeral=True)

        try:
            uid = int(user_id)
            user = await bot.fetch_user(uid)
            await interaction.guild.ban(
                user,
                delete_message_seconds=delete_messages.value,
                reason=f"{reason} | par {interaction.user}",
            )
            _add_blacklist(uid)
            await interaction.response.send_message(f"✅ ID `{uid}` banni.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)

    @bot.tree.command(name="banip", description="Alias ban ID (Discord ne donne pas les IP)")
    @app_commands.describe(user_id="ID utilisateur", delete_messages="Messages à supprimer", reason="Raison")
    @app_commands.choices(delete_messages=DELETE_MESSAGE_CHOICES)
    async def slash_banip(
        interaction: discord.Interaction,
        user_id: str,
        delete_messages: app_commands.Choice[int],
        reason: str,
    ):
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message("Serveur invalide.", ephemeral=True)

        if not interaction.user.guild_permissions.ban_members:
            return await interaction.response.send_message("❌ Pas de permission.", ephemeral=True)

        try:
            uid = int(user_id)
            user = await bot.fetch_user(uid)
            await interaction.guild.ban(
                user,
                delete_message_seconds=delete_messages.value,
                reason=f"{reason} | par {interaction.user} | alias /banip",
            )
            _add_blacklist(uid)
            await interaction.response.send_message(
                "✅ `/banip` exécuté comme **ban ID + blacklist**.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)

    @bot.tree.command(name="unbanid", description="Débannir un utilisateur par ID")
    @app_commands.describe(user_id="ID utilisateur")
    async def slash_unbanid(interaction: discord.Interaction, user_id: str):
        if not interaction.guild or interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message("Serveur invalide.", ephemeral=True)

        if not interaction.user.guild_permissions.ban_members:
            return await interaction.response.send_message("❌ Pas de permission.", ephemeral=True)

        try:
            uid = int(user_id)
            user = await bot.fetch_user(uid)
            await interaction.guild.unban(user, reason=f"Unban par {interaction.user}")
            _remove_blacklist(uid)
            await interaction.response.send_message(f"✅ ID `{uid}` débanni.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)

    @bot.command()
    async def raid(ctx, mode: str):
        if not ctx.author.guild_permissions.administrator:
            return await ctx.send("❌ Pas de permission.")

        mode = mode.lower().strip()
        if mode == "on":
            await bot.enable_raid_mode_helper(bot, ctx.guild, f"Activation manuelle par {ctx.author}")
            await ctx.send("🚨 Mode raid activé.")
        elif mode == "off":
            await bot.disable_raid_mode_helper(bot, ctx.guild)
            await ctx.send("✅ Mode raid désactivé.")
        else:
            await ctx.send("Utilise `!raid on` ou `!raid off`.")
