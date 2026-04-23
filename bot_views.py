import os
import asyncio
import random
from datetime import timedelta

import discord

from config import (
    DATA_DIR,
    GIVEAWAYS_FILE,
    TICKETS_FILE,
    VERIFY_FILE,
    GIVEAWAY_PUBLIC_CHANNEL_ID,
    TICKET_CATEGORY_ID,
    TICKET_STAFF_ROLE_ID,
    TICKET_LOG_CHANNEL_ID,
    VERIFIED_ROLE_ID,
    UNVERIFIED_ROLE_ID,
    MAX_VERIFY_TRIES,
    VERIFY_CHANNEL_ID,
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


async def safe_fetch_message(channel: discord.TextChannel, message_id: int):
    try:
        return await channel.fetch_message(message_id)
    except Exception:
        return None


async def log_ticket_action(guild: discord.Guild, content: str):
    channel = guild.get_channel(TICKET_LOG_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(content)
        except Exception:
            pass


class GiveawayCreateModal(discord.ui.Modal, title="Créer un giveaway"):
    prize = discord.ui.TextInput(
        label="Lot",
        placeholder="Exemple : Nitro 1 mois",
        max_length=100,
    )
    duration_minutes = discord.ui.TextInput(
        label="Durée en minutes",
        placeholder="Exemple : 60",
        max_length=10,
    )
    winners_count = discord.ui.TextInput(
        label="Nombre de gagnants",
        placeholder="Exemple : 1",
        max_length=3,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_giveaway_staff(interaction.user):
            return await interaction.response.send_message(
                "Seuls les modérateurs peuvent créer un giveaway.",
                ephemeral=True,
            )

        try:
            duration = int(str(self.duration_minutes))
            winners = int(str(self.winners_count))
        except ValueError:
            return await interaction.response.send_message("Valeurs invalides.", ephemeral=True)

        if duration <= 0 or winners <= 0:
            return await interaction.response.send_message(
                "Les valeurs doivent être supérieures à 0.",
                ephemeral=True,
            )

        public_channel = interaction.guild.get_channel(GIVEAWAY_PUBLIC_CHANNEL_ID)
        if not isinstance(public_channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Salon giveaways introuvable.",
                ephemeral=True,
            )

        end_at = now_utc() + timedelta(minutes=duration)

        embed = discord.Embed(
            title="🎉 GIVEAWAY",
            description=(
                f"**Lot :** {self.prize}\n"
                f"**Gagnants :** {winners}\n"
                f"**Fin :** {ts_full(end_at)} ({ts_relative(end_at)})\n\n"
                f"Clique sur **Participer**."
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Créé par {interaction.user}")

        msg = await public_channel.send(embed=embed, view=GiveawayJoinView())

        data = load_json(GIVEAWAYS_FILE, {"giveaways": {}, "staff_panel_message_id": None})
        data["giveaways"][str(msg.id)] = {
            "guild_id": interaction.guild.id,
            "channel_id": public_channel.id,
            "message_id": msg.id,
            "prize": str(self.prize),
            "winners_count": winners,
            "end_at": dt_to_iso(end_at),
            "ended": False,
            "participants": [],
            "created_by": interaction.user.id,
            "winner_ids": [],
        }
        save_json(GIVEAWAYS_FILE, data)

        await interaction.response.send_message(
            f"Giveaway créé dans {public_channel.mention}",
            ephemeral=True,
        )


class GiveawayStaffPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Créer un giveaway",
        emoji="🎉",
        style=discord.ButtonStyle.success,
        custom_id="giveaway_staff_create",
    )
    async def create_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_giveaway_staff(interaction.user):
            return await interaction.response.send_message(
                "Seuls les modérateurs peuvent créer un giveaway.",
                ephemeral=True,
            )

        await interaction.response.send_modal(GiveawayCreateModal())


class GiveawayJoinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Participer",
        emoji="🎊",
        style=discord.ButtonStyle.primary,
        custom_id="giveaway_join_button",
    )
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_json(GIVEAWAYS_FILE, {"giveaways": {}, "staff_panel_message_id": None})
        giveaway = data["giveaways"].get(str(interaction.message.id))

        if not giveaway:
            return await interaction.response.send_message("Giveaway introuvable.", ephemeral=True)

        if giveaway["ended"]:
            return await interaction.response.send_message("Ce giveaway est terminé.", ephemeral=True)

        if interaction.user.id in giveaway["participants"]:
            return await interaction.response.send_message("Tu participes déjà.", ephemeral=True)

        giveaway["participants"].append(interaction.user.id)
        save_json(GIVEAWAYS_FILE, data)

        await interaction.response.send_message("Participation enregistrée. 🎉", ephemeral=True)


class GiveawayEndedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Reroll",
        emoji="🔁",
        style=discord.ButtonStyle.secondary,
        custom_id="giveaway_reroll_button",
    )
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_giveaway_staff(interaction.user):
            return await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)

        data = load_json(GIVEAWAYS_FILE, {"giveaways": {}, "staff_panel_message_id": None})
        giveaway = data["giveaways"].get(str(interaction.message.id))
        if not giveaway or not giveaway["participants"]:
            return await interaction.response.send_message("Aucun participant.", ephemeral=True)

        winners_count = min(giveaway["winners_count"], len(giveaway["participants"]))
        winners = random.sample(giveaway["participants"], winners_count)
        giveaway["winner_ids"] = winners
        save_json(GIVEAWAYS_FILE, data)

        mentions = ", ".join(f"<@{uid}>" for uid in winners)
        await interaction.channel.send(
            f"🔁 **Reroll** du giveaway **{giveaway['prize']}**\nNouveau(x) gagnant(s) : {mentions}"
        )
        await interaction.response.send_message("Reroll effectué.", ephemeral=True)


async def finish_giveaway(bot: discord.Client, message_id: str):
    data = load_json(GIVEAWAYS_FILE, {"giveaways": {}, "staff_panel_message_id": None})
    giveaway = data["giveaways"].get(message_id)
    if not giveaway or giveaway["ended"]:
        return

    channel = bot.get_channel(giveaway["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        giveaway["ended"] = True
        save_json(GIVEAWAYS_FILE, data)
        return

    message = await safe_fetch_message(channel, giveaway["message_id"])
    participants = giveaway["participants"]
    winners_count = min(giveaway["winners_count"], len(participants))

    if winners_count > 0:
        winners = random.sample(participants, winners_count)
        winner_mentions = ", ".join(f"<@{uid}>" for uid in winners)
    else:
        winners = []
        winner_mentions = "Aucun gagnant"

    giveaway["ended"] = True
    giveaway["winner_ids"] = winners
    save_json(GIVEAWAYS_FILE, data)

    embed = discord.Embed(
        title="🎉 GIVEAWAY TERMINÉ",
        description=(
            f"**Lot :** {giveaway['prize']}\n"
            f"**Gagnant(s) :** {winner_mentions}\n"
            f"**Participants :** {len(participants)}"
        ),
        color=discord.Color.red(),
    )

    if message:
        try:
            await message.edit(embed=embed, view=GiveawayEndedView())
        except Exception:
            pass

    await channel.send(
        f"🎉 Giveaway terminé pour **{giveaway['prize']}**\nGagnant(s) : {winner_mentions}"
    )


class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ouvrir un ticket",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="ticket_open_button",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_verified(interaction.user):
            return await interaction.response.send_message(
                "Tu dois être vérifié avant d'ouvrir un ticket.",
                ephemeral=True,
            )

        guild = interaction.guild
        member = interaction.user
        tdata = load_json(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})

        for channel_id, info in tdata["tickets"].items():
            if info["guild_id"] == guild.id and info["owner_id"] == member.id:
                existing = guild.get_channel(int(channel_id))
                if existing:
                    return await interaction.response.send_message(
                        f"Tu as déjà un ticket ouvert : {existing.mention}",
                        ephemeral=True,
                    )

        category = guild.get_channel(TICKET_CATEGORY_ID)
        staff_role = guild.get_role(TICKET_STAFF_ROLE_ID)

        if not isinstance(category, discord.CategoryChannel) or not staff_role:
            return await interaction.response.send_message(
                "Configuration ticket invalide.",
                ephemeral=True,
            )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            staff_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            ),
        }

        channel = await guild.create_text_channel(
            name=sanitize_channel_name(f"ticket-{member.name}"),
            category=category,
            overwrites=overwrites,
            reason=f"Ticket créé pour {member}",
        )

        tdata["tickets"][str(channel.id)] = {
            "guild_id": guild.id,
            "owner_id": member.id,
            "created_at": dt_to_iso(now_utc()),
        }
        save_json(TICKETS_FILE, tdata)

        embed = discord.Embed(
            title="🎫 Ticket ouvert",
            description=f"Bonjour {member.mention}, explique ton problème ici.",
            color=discord.Color.green(),
        )

        await channel.send(
            content=f"{member.mention} <@&{TICKET_STAFF_ROLE_ID}>",
            embed=embed,
            view=TicketManageView(),
        )
        await log_ticket_action(guild, f"📂 Ticket créé : {channel.mention} par {member.mention}")
        await interaction.response.send_message(f"Ticket créé : {channel.mention}", ephemeral=True)


async def build_transcript_file(channel: discord.TextChannel) -> str:
    path = os.path.join(DATA_DIR, f"transcript_{channel.id}.txt")
    lines = []

    async for msg in channel.history(limit=None, oldest_first=True):
        created = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        content = msg.content if msg.content else ""
        attachment_text = ""
        if msg.attachments:
            attachment_text = " | pièces jointes : " + ", ".join(a.url for a in msg.attachments)
        lines.append(f"[{created}] {msg.author} : {content}{attachment_text}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) if lines else "Aucun message.")
    return path


async def export_transcript(channel: discord.TextChannel, guild: discord.Guild):
    path = await build_transcript_file(channel)
    log_channel = guild.get_channel(TICKET_LOG_CHANNEL_ID)
    if isinstance(log_channel, discord.TextChannel):
        try:
            await log_channel.send(content=f"📄 Transcript de #{channel.name}", file=discord.File(path))
        except Exception:
            pass


class TicketManageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fermer", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ticket_close_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        tdata = load_json(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})
        info = tdata["tickets"].get(str(interaction.channel.id))
        if not info:
            return await interaction.response.send_message("Ce salon n'est pas un ticket.", ephemeral=True)

        allowed = interaction.user.id == info["owner_id"] or is_ticket_staff(interaction.user)
        if not allowed:
            return await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)

        await interaction.response.send_message("Fermeture du ticket dans 3 secondes...")
        await asyncio.sleep(3)

        await export_transcript(interaction.channel, interaction.guild)

        tdata["tickets"].pop(str(interaction.channel.id), None)
        save_json(TICKETS_FILE, tdata)

        await log_ticket_action(
            interaction.guild,
            f"🗑️ Ticket fermé : #{interaction.channel.name} par {interaction.user.mention}",
        )
        await interaction.channel.delete(reason=f"Ticket fermé par {interaction.user}")

    @discord.ui.button(label="Transcript", emoji="📄", style=discord.ButtonStyle.secondary, custom_id="ticket_transcript_button")
    async def transcript_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        tdata = load_json(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})
        info = tdata["tickets"].get(str(interaction.channel.id))
        if not info:
            return await interaction.response.send_message("Ce salon n'est pas un ticket.", ephemeral=True)

        allowed = interaction.user.id == info["owner_id"] or is_ticket_staff(interaction.user)
        if not allowed:
            return await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)

        file_path = await build_transcript_file(interaction.channel)
        await interaction.response.send_message(
            "Transcript généré.",
            file=discord.File(file_path),
            ephemeral=True,
        )


class VerifyRetryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Entrer le code", emoji="✍️", style=discord.ButtonStyle.primary)
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyCaptchaModal())


class VerifyCaptchaModal(discord.ui.Modal, title="Validation du captcha"):
    captcha_input = discord.ui.TextInput(
        label="Recopie le captcha",
        placeholder="Exemple : A1B2C3",
        max_length=12,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        vdata = load_json(VERIFY_FILE, {"users": {}, "panel_message_id": None})
        entry = vdata["users"].get(str(interaction.user.id))
        if not entry:
            return await interaction.response.send_message("Aucune vérification en attente.", ephemeral=True)

        expected = entry["captcha"].strip().upper()
        given = str(self.captcha_input).strip().upper()

        if given != expected:
            entry["tries"] = entry.get("tries", 0) + 1
            entry["captcha"] = random_code()
            entry["updated_at"] = dt_to_iso(now_utc())
            save_json(VERIFY_FILE, vdata)

            tries_left = max(0, MAX_VERIFY_TRIES - entry["tries"])
            if tries_left <= 0:
                try:
                    await interaction.guild.kick(interaction.user, reason="Captcha incorrect trop de fois")
                except Exception:
                    pass
                return await interaction.response.send_message(
                    "❌ Trop d'erreurs. Tu as été expulsé.",
                    ephemeral=True,
                )

            image_file = captcha_discord_file(entry["captcha"])
            embed = discord.Embed(
                title="❌ Captcha incorrect",
                description=f"Il te reste **{tries_left} essai(s)**.\nUn nouveau captcha a été généré.",
                color=discord.Color.red(),
            )
            embed.set_image(url="attachment://captcha.png")
            return await interaction.response.send_message(
                embed=embed,
                file=image_file,
                ephemeral=True,
                view=VerifyRetryView(),
            )

        guild = interaction.guild
        verified_role = guild.get_role(VERIFIED_ROLE_ID)
        unverified_role = guild.get_role(UNVERIFIED_ROLE_ID)

        if not verified_role or not unverified_role:
            return await interaction.response.send_message(
                "Rôles de vérification introuvables.",
                ephemeral=True,
            )

        try:
            if unverified_role in interaction.user.roles:
                await interaction.user.remove_roles(unverified_role, reason="Captcha validé")
            if verified_role not in interaction.user.roles:
                await interaction.user.add_roles(verified_role, reason="Captcha validé")
        except Exception:
            return await interaction.response.send_message(
                "Impossible de modifier les rôles.",
                ephemeral=True,
            )

        entry["verified"] = True
        entry["updated_at"] = dt_to_iso(now_utc())
        save_json(VERIFY_FILE, vdata)

        done_embed = discord.Embed(
            title="✅ Vérification réussie",
            description="Tu as maintenant accès au serveur.",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=done_embed, ephemeral=True)


class VerifyPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Commencer la vérification", emoji="🛡️", style=discord.ButtonStyle.success, custom_id="verify_open_button")
    async def verify_open(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if is_verified(interaction.user):
            return await interaction.response.send_message("Tu es déjà vérifié.", ephemeral=True)

        vdata = load_json(VERIFY_FILE, {"users": {}, "panel_message_id": None})
        code = random_code()

        vdata["users"][str(interaction.user.id)] = {
            "captcha": code,
            "verified": False,
            "tries": 0,
            "created_at": dt_to_iso(now_utc()),
            "updated_at": dt_to_iso(now_utc()),
        }
        save_json(VERIFY_FILE, vdata)

        image_file = captcha_discord_file(code)

        embed = discord.Embed(
            title=f"Bienvenue sur {interaction.guild.name}",
            description=(
                "Pour accéder au serveur, complète le captcha.\n\n"
                f"**Temps limite :** 5 minutes\n"
                f"**Essais max :** {MAX_VERIFY_TRIES}"
            ),
            color=discord.Color.orange(),
        )
        embed.set_image(url="attachment://captcha.png")
        embed.set_footer(text="Clique sur Entrer le code pour valider.")

        await interaction.response.send_message(
            embed=embed,
            file=image_file,
            ephemeral=True,
            view=VerifyRetryView(),
        )


async def ensure_panels(bot: discord.Client):
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    # Giveaway staff panel
    giveaway_channel = guild.get_channel(GIVEAWAY_PUBLIC_CHANNEL_ID)
    if isinstance(giveaway_channel, discord.TextChannel):
        data = load_json(GIVEAWAYS_FILE, {"giveaways": {}, "staff_panel_message_id": None})
        message_id = data.get("staff_panel_message_id")
        if not message_id or not await safe_fetch_message(giveaway_channel, message_id):
            embed = discord.Embed(
                title="🎉 Giveaway Staff",
                description="Bouton réservé aux modérateurs pour créer un giveaway.",
                color=discord.Color.blurple(),
            )
            msg = await giveaway_channel.send(embed=embed, view=GiveawayStaffPanelView())
            data["staff_panel_message_id"] = msg.id
            save_json(GIVEAWAYS_FILE, data)

    # Ticket panel
    ticket_channel = guild.get_channel(TICKET_PANEL_CHANNEL_ID)
    if isinstance(ticket_channel, discord.TextChannel):
        data = load_json(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})
        message_id = data.get("panel_message_id")
        if not message_id or not await safe_fetch_message(ticket_channel, message_id):
            embed = discord.Embed(
                title="🎫 Support",
                description="Clique sur le bouton ci-dessous pour ouvrir un ticket privé.",
                color=discord.Color.green(),
            )
            msg = await ticket_channel.send(embed=embed, view=TicketOpenView())
            data["panel_message_id"] = msg.id
            save_json(TICKETS_FILE, data)

    # Verify panel
    verify_channel = guild.get_channel(VERIFY_CHANNEL_ID)
    if isinstance(verify_channel, discord.TextChannel):
        data = load_json(VERIFY_FILE, {"users": {}, "panel_message_id": None})
        message_id = data.get("panel_message_id")
        if not message_id or not await safe_fetch_message(verify_channel, message_id):
            embed = discord.Embed(
                title="🛡️ Vérification",
                description="Clique sur le bouton pour lancer la vérification captcha.",
                color=discord.Color.orange(),
            )
            msg = await verify_channel.send(embed=embed, view=VerifyPanelView())
            data["panel_message_id"] = msg.id
            save_json(VERIFY_FILE, data)
