import os
import json
import asyncio
import random
import string
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

# =========================================================
# CONFIG
# =========================================================
# Logs arrivée / départ
MEMBER_LOG_CHANNEL_ID = 1496544952161665215

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("La variable d'environnement TOKEN est introuvable.")

GUILD_ID = 1336409517298286612

# =========================
# GIVEAWAYS
# =========================

# Salon STAFF où seul le staff voit le bouton "Créer un giveaway"
GIVEAWAY_STAFF_PANEL_CHANNEL_ID = 1496178317739556994

# Salon PUBLIC où les giveaways sont envoyés pour participation
GIVEAWAY_PUBLIC_CHANNEL_ID = 1496178317739556994

# Rôle autorisé à créer / reroll les giveaways
GIVEAWAY_ALLOWED_ROLE_ID = 1496179072005443644

# =========================
# TICKETS
# =========================

TICKET_PANEL_CHANNEL_ID = 1496178317739556994
TICKET_CATEGORY_ID = 1496178590080040960
TICKET_STAFF_ROLE_ID = 1496179072005443644
TICKET_LOG_CHANNEL_ID = 1496178736217854002

# =========================
# VERIFY / CAPTCHA
# =========================

VERIFY_CHANNEL_ID = 1496179758969651390
UNVERIFIED_ROLE_ID = 1496179992651104506
VERIFIED_ROLE_ID = 1496179832717836368

VERIFY_TIMEOUT_SECONDS = 300
AUTO_KICK_UNVERIFIED = True
MAX_VERIFY_TRIES = 3

# =========================
# SERVER STATS
# =========================

ALL_MEMBERS_CHANNEL_ID = 0
MEMBERS_CHANNEL_ID = 0
BOTS_CHANNEL_ID = 0

# =========================
# FILES
# =========================

DATA_DIR = "bot_data"
GIVEAWAYS_FILE = os.path.join(DATA_DIR, "giveaways.json")
TICKETS_FILE = os.path.join(DATA_DIR, "tickets.json")
VERIFY_FILE = os.path.join(DATA_DIR, "verify.json")

# =========================================================
# SETUP
# =========================================================

os.makedirs(DATA_DIR, exist_ok=True)

def ensure_file(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=4, ensure_ascii=False)

ensure_file(GIVEAWAYS_FILE, {"giveaways": {}, "staff_panel_message_id": None})
ensure_file(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})
ensure_file(VERIFY_FILE, {"users": {}, "panel_message_id": None})

def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def now_utc():
    return datetime.now(timezone.utc)

def dt_to_iso(dt: datetime):
    return dt.isoformat()

def iso_to_dt(value: str):
    return datetime.fromisoformat(value)

def ts_full(dt: datetime):
    return f"<t:{int(dt.timestamp())}:F>"

def ts_relative(dt: datetime):
    return f"<t:{int(dt.timestamp())}:R>"

def random_code(length=6):
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))

def sanitize_channel_name(text: str) -> str:
    text = text.lower().replace(" ", "-")
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
    text = "".join(c for c in text if c in allowed)
    return text[:80] if text else "ticket"

# =========================================================
# BOT
# =========================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)

# =========================================================
# HELPERS
# =========================================================

def has_role(member: discord.Member, role_id: int) -> bool:
    return any(role.id == role_id for role in member.roles)

def is_giveaway_staff(member: discord.Member) -> bool:
    return has_role(member, GIVEAWAY_ALLOWED_ROLE_ID) or member.guild_permissions.administrator

def is_ticket_staff(member: discord.Member) -> bool:
    return has_role(member, TICKET_STAFF_ROLE_ID) or member.guild_permissions.administrator

def is_verified(member: discord.Member) -> bool:
    return has_role(member, VERIFIED_ROLE_ID)

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

# =========================================================
# GIVEAWAYS
# =========================================================

class GiveawayCreateModal(discord.ui.Modal, title="Créer un giveaway"):
    prize = discord.ui.TextInput(
        label="Lot",
        placeholder="Exemple : Nitro 1 mois",
        max_length=100
    )
    duration_minutes = discord.ui.TextInput(
        label="Durée en minutes",
        placeholder="Exemple : 60",
        max_length=10
    )
    winners_count = discord.ui.TextInput(
        label="Nombre de gagnants",
        placeholder="Exemple : 1",
        max_length=3
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_giveaway_staff(interaction.user):
            return await interaction.response.send_message(
                "Seuls les modérateurs peuvent créer un giveaway.",
                ephemeral=True
            )

        try:
            duration = int(str(self.duration_minutes))
            winners = int(str(self.winners_count))
        except ValueError:
            return await interaction.response.send_message(
                "Durée et nombre de gagnants doivent être des nombres.",
                ephemeral=True
            )

        if duration <= 0 or winners <= 0:
            return await interaction.response.send_message(
                "Les valeurs doivent être supérieures à 0.",
                ephemeral=True
            )

        public_channel = interaction.guild.get_channel(GIVEAWAY_PUBLIC_CHANNEL_ID)
        if not isinstance(public_channel, discord.TextChannel):
            return await interaction.response.send_message(
                "Le salon public giveaway est introuvable.",
                ephemeral=True
            )

        end_at = now_utc() + timedelta(minutes=duration)

        embed = discord.Embed(
            title="🎉 GIVEAWAY",
            description=(
                f"**Lot :** {self.prize}\n"
                f"**Gagnants :** {winners}\n"
                f"**Fin :** {ts_full(end_at)} ({ts_relative(end_at)})\n\n"
                f"Clique sur **Participer** pour rejoindre."
            ),
            color=discord.Color.gold()
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
            "winner_ids": []
        }
        save_json(GIVEAWAYS_FILE, data)

        await interaction.response.send_message(
            f"Giveaway créé dans {public_channel.mention}",
            ephemeral=True
        )

class GiveawayStaffPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Créer un giveaway",
        emoji="🎉",
        style=discord.ButtonStyle.success,
        custom_id="giveaway_staff_create"
    )
    async def create_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_giveaway_staff(interaction.user):
            return await interaction.response.send_message(
                "Seuls les modérateurs peuvent créer un giveaway.",
                ephemeral=True
            )

        await interaction.response.send_modal(GiveawayCreateModal())

class GiveawayJoinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Participer",
        emoji="🎊",
        style=discord.ButtonStyle.primary,
        custom_id="giveaway_join_button"
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
        custom_id="giveaway_reroll_button"
    )
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_giveaway_staff(interaction.user):
            return await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)

        data = load_json(GIVEAWAYS_FILE, {"giveaways": {}, "staff_panel_message_id": None})
        giveaway = data["giveaways"].get(str(interaction.message.id))

        if not giveaway:
            return await interaction.response.send_message("Giveaway introuvable.", ephemeral=True)

        participants = giveaway["participants"]
        if not participants:
            return await interaction.response.send_message("Aucun participant.", ephemeral=True)

        winners_count = min(giveaway["winners_count"], len(participants))
        winners = random.sample(participants, winners_count)
        giveaway["winner_ids"] = winners
        save_json(GIVEAWAYS_FILE, data)

        mentions = ", ".join(f"<@{uid}>" for uid in winners)

        await interaction.channel.send(
            f"🔁 **Reroll** du giveaway **{giveaway['prize']}**\nNouveau(x) gagnant(s) : {mentions}"
        )
        await interaction.response.send_message("Reroll effectué.", ephemeral=True)

async def finish_giveaway(message_id: str):
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
        color=discord.Color.red()
    )

    if message:
        try:
            await message.edit(embed=embed, view=GiveawayEndedView())
        except Exception:
            pass

    await channel.send(
        f"🎉 Giveaway terminé pour **{giveaway['prize']}**\nGagnant(s) : {winner_mentions}"
    )

@tasks.loop(seconds=15)
async def giveaway_watcher():
    data = load_json(GIVEAWAYS_FILE, {"giveaways": {}, "staff_panel_message_id": None})
    current = now_utc()

    for message_id, giveaway in list(data["giveaways"].items()):
        if giveaway.get("ended"):
            continue

        try:
            end_at = iso_to_dt(giveaway["end_at"])
        except Exception:
            continue

        if current >= end_at:
            await finish_giveaway(message_id)

# =========================================================
# TICKETS
# =========================================================

class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ouvrir un ticket",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="ticket_open_button"
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_verified(interaction.user):
            return await interaction.response.send_message(
                "Tu dois être vérifié avant d'ouvrir un ticket.",
                ephemeral=True
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
                        ephemeral=True
                    )

        category = guild.get_channel(TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("Catégorie ticket introuvable.", ephemeral=True)

        staff_role = guild.get_role(TICKET_STAFF_ROLE_ID)
        if not staff_role:
            return await interaction.response.send_message("Rôle staff introuvable.", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True
            ),
            staff_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True
            )
        }

        cname = sanitize_channel_name(f"ticket-{member.name}")
        channel = await guild.create_text_channel(
            name=cname,
            category=category,
            overwrites=overwrites,
            reason=f"Ticket créé pour {member}"
        )

        tdata["tickets"][str(channel.id)] = {
            "guild_id": guild.id,
            "owner_id": member.id,
            "created_at": dt_to_iso(now_utc()),
            "members_added": []
        }
        save_json(TICKETS_FILE, tdata)

        embed = discord.Embed(
            title="🎫 Ticket ouvert",
            description=f"Bonjour {member.mention}, explique ton problème ici.",
            color=discord.Color.green()
        )

        await channel.send(
            content=f"{member.mention} <@&{TICKET_STAFF_ROLE_ID}>",
            embed=embed,
            view=TicketManageView()
        )

        await log_ticket_action(guild, f"📂 Ticket créé : {channel.mention} par {member.mention}")
        await interaction.response.send_message(f"Ticket créé : {channel.mention}", ephemeral=True)

class AddUserModal(discord.ui.Modal, title="Ajouter un membre au ticket"):
    user_id_input = discord.ui.TextInput(
        label="ID utilisateur",
        placeholder="Colle l'ID Discord du membre",
        max_length=25
    )

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_ticket_staff(interaction.user):
            return await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)

        try:
            user_id = int(str(self.user_id_input))
        except ValueError:
            return await interaction.response.send_message("ID invalide.", ephemeral=True)

        channel = interaction.guild.get_channel(self.channel_id)
        member = interaction.guild.get_member(user_id)

        if not isinstance(channel, discord.TextChannel) or not member:
            return await interaction.response.send_message("Salon ou membre introuvable.", ephemeral=True)

        overwrite = channel.overwrites_for(member)
        overwrite.view_channel = True
        overwrite.send_messages = True
        overwrite.read_message_history = True
        await channel.set_permissions(member, overwrite=overwrite)

        tdata = load_json(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})
        info = tdata["tickets"].get(str(channel.id))
        if info and user_id not in info["members_added"]:
            info["members_added"].append(user_id)
            save_json(TICKETS_FILE, tdata)

        await log_ticket_action(interaction.guild, f"➕ {member.mention} ajouté à {channel.mention} par {interaction.user.mention}")
        await interaction.response.send_message(f"{member.mention} a été ajouté.", ephemeral=True)

class RemoveUserModal(discord.ui.Modal, title="Retirer un membre du ticket"):
    user_id_input = discord.ui.TextInput(
        label="ID utilisateur",
        placeholder="Colle l'ID Discord du membre",
        max_length=25
    )

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_ticket_staff(interaction.user):
            return await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)

        try:
            user_id = int(str(self.user_id_input))
        except ValueError:
            return await interaction.response.send_message("ID invalide.", ephemeral=True)

        channel = interaction.guild.get_channel(self.channel_id)
        member = interaction.guild.get_member(user_id)

        if not isinstance(channel, discord.TextChannel) or not member:
            return await interaction.response.send_message("Salon ou membre introuvable.", ephemeral=True)

        await channel.set_permissions(member, overwrite=None)

        tdata = load_json(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})
        info = tdata["tickets"].get(str(channel.id))
        if info and user_id in info["members_added"]:
            info["members_added"].remove(user_id)
            save_json(TICKETS_FILE, tdata)

        await log_ticket_action(interaction.guild, f"➖ {member.mention} retiré de {channel.mention} par {interaction.user.mention}")
        await interaction.response.send_message(f"{member.mention} a été retiré.", ephemeral=True)

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

    @discord.ui.button(
        label="Fermer",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close_button"
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if (
            not interaction.guild
            or not isinstance(interaction.user, discord.Member)
            or not isinstance(interaction.channel, discord.TextChannel)
        ):
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

        tdata = load_json(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})
        tdata["tickets"].pop(str(interaction.channel.id), None)
        save_json(TICKETS_FILE, tdata)

        await log_ticket_action(interaction.guild, f"🗑️ Ticket fermé : #{interaction.channel.name} par {interaction.user.mention}")
        await interaction.channel.delete(reason=f"Ticket fermé par {interaction.user}")

    @discord.ui.button(
        label="Transcript",
        emoji="📄",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_transcript_button"
    )
    async def transcript_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if (
            not interaction.guild
            or not isinstance(interaction.user, discord.Member)
            or not isinstance(interaction.channel, discord.TextChannel)
        ):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        tdata = load_json(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})
        info = tdata["tickets"].get(str(interaction.channel.id))
        if not info:
            return await interaction.response.send_message("Ce salon n'est pas un ticket.", ephemeral=True)

        allowed = interaction.user.id == info["owner_id"] or is_ticket_staff(interaction.user)
        if not allowed:
            return await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)

        file_path = await build_transcript_file(interaction.channel)
        await interaction.response.send_message("Transcript généré.", file=discord.File(file_path), ephemeral=True)

    @discord.ui.button(
        label="Ajouter",
        emoji="➕",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_add_user_button"
    )
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if (
            not interaction.guild
            or not isinstance(interaction.user, discord.Member)
            or not isinstance(interaction.channel, discord.TextChannel)
        ):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_ticket_staff(interaction.user):
            return await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)

        await interaction.response.send_modal(AddUserModal(interaction.channel.id))

    @discord.ui.button(
        label="Retirer",
        emoji="➖",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_remove_user_button"
    )
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if (
            not interaction.guild
            or not isinstance(interaction.user, discord.Member)
            or not isinstance(interaction.channel, discord.TextChannel)
        ):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)

        if not is_ticket_staff(interaction.user):
            return await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)

        await interaction.response.send_modal(RemoveUserModal(interaction.channel.id))

# =========================================================
# VERIFY / CAPTCHA
# =========================================================

class VerifyCaptchaModal(discord.ui.Modal, title="Vérification du serveur"):
    captcha_input = discord.ui.TextInput(
        label="Recopie le captcha",
        placeholder="Exemple : A1B2C3",
        max_length=12
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
                    "Captcha incorrect trop de fois. Expulsion.",
                    ephemeral=True
                )

            embed = discord.Embed(
                title="❌ Captcha incorrect",
                description=(
                    f"Il te reste **{tries_left} essai(s)**.\n"
                    f"**Nouveau captcha :** `{entry['captcha']}`"
                ),
                color=discord.Color.red()
            )
            embed.set_footer(text="Clique sur Réessayer pour entrer le nouveau code.")

            return await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
                view=VerifyRetryView()
            )

        guild = interaction.guild
        verified_role = guild.get_role(VERIFIED_ROLE_ID)
        unverified_role = guild.get_role(UNVERIFIED_ROLE_ID)

        if not verified_role or not unverified_role:
            return await interaction.response.send_message("Rôles de vérification introuvables.", ephemeral=True)

        try:
            if unverified_role in interaction.user.roles:
                await interaction.user.remove_roles(unverified_role, reason="Captcha validé")
            if verified_role not in interaction.user.roles:
                await interaction.user.add_roles(verified_role, reason="Captcha validé")
        except Exception:
            return await interaction.response.send_message("Impossible de modifier les rôles.", ephemeral=True)

        entry["verified"] = True
        entry["updated_at"] = dt_to_iso(now_utc())
        save_json(VERIFY_FILE, vdata)

        embed = discord.Embed(
            title="✅ Vérification réussie",
            description="Bienvenue sur le serveur. Tu as maintenant accès aux salons.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class VerifyRetryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Réessayer", emoji="🔁", style=discord.ButtonStyle.primary)
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyCaptchaModal())

class VerifyPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Commencer la vérification",
        emoji="🛡️",
        style=discord.ButtonStyle.success,
        custom_id="verify_open_button"
    )
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
            "updated_at": dt_to_iso(now_utc())
        }
        save_json(VERIFY_FILE, vdata)

        embed = discord.Embed(
            title=f"Bienvenue sur {interaction.guild.name}",
            description=(
                "Pour accéder au serveur, complète le captcha avec **6 caractères majuscules**.\n\n"
                f"**Captcha :** `{code}`\n"
                f"**Expiration automatique :** dans 5 minutes\n"
                f"**Essais max :** {MAX_VERIFY_TRIES}"
            ),
            color=discord.Color.orange()
        )
        embed.set_footer(text="Clique sur le bouton ci-dessous pour entrer le code.")

        await interaction.response.send_message(embed=embed, ephemeral=True, view=VerifyRetryView())

@tasks.loop(seconds=30)
async def verify_timeout_watcher():
    if not AUTO_KICK_UNVERIFIED:
        return

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    vdata = load_json(VERIFY_FILE, {"users": {}, "panel_message_id": None})
    changed = False
    current_time = now_utc()

    for user_id, entry in list(vdata["users"].items()):
        if entry.get("verified"):
            continue

        created_at_raw = entry.get("created_at") or entry.get("updated_at")
        if not created_at_raw:
            continue

        try:
            created_at = iso_to_dt(created_at_raw)
        except Exception:
            continue

        if (current_time - created_at).total_seconds() >= VERIFY_TIMEOUT_SECONDS:
            member = guild.get_member(int(user_id))
            if member:
                try:
                    await guild.kick(member, reason="Non vérifié après 5 minutes")
                except Exception:
                    pass

            vdata["users"].pop(user_id, None)
            changed = True

    if changed:
        save_json(VERIFY_FILE, vdata)

# =========================================================
# SERVER STATS
# =========================================================

async def update_server_stats_once():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    all_members = guild.member_count or 0
    humans = len([m for m in guild.members if not m.bot])
    bots_count = len([m for m in guild.members if m.bot])

    all_channel = guild.get_channel(ALL_MEMBERS_CHANNEL_ID)
    members_channel = guild.get_channel(MEMBERS_CHANNEL_ID)
    bots_channel = guild.get_channel(BOTS_CHANNEL_ID)

    try:
        if all_channel and ALL_MEMBERS_CHANNEL_ID != 0:
            new_name = f"📊 All Members : {all_members}"
            if all_channel.name != new_name:
                await all_channel.edit(name=new_name)

        if members_channel and MEMBERS_CHANNEL_ID != 0:
            new_name = f"👥 Members : {humans}"
            if members_channel.name != new_name:
                await members_channel.edit(name=new_name)

        if bots_channel and BOTS_CHANNEL_ID != 0:
            new_name = f"🤖 Bots : {bots_count}"
            if bots_channel.name != new_name:
                await bots_channel.edit(name=new_name)
    except Exception as e:
        print("Erreur server stats :", e)

@tasks.loop(minutes=1)
async def update_server_stats_loop():
    await update_server_stats_once()

# =========================================================
# PANELS
# =========================================================

async def ensure_panel(channel: discord.TextChannel, kind: str):
    if kind == "giveaway_staff":
        data = load_json(GIVEAWAYS_FILE, {"giveaways": {}, "staff_panel_message_id": None})
        message_id = data.get("staff_panel_message_id")
        if message_id:
            msg = await safe_fetch_message(channel, message_id)
            if msg:
                return

        embed = discord.Embed(
            title="🎉 Giveaway Staff",
            description="Bouton réservé aux modérateurs pour créer un giveaway.",
            color=discord.Color.blurple()
        )
        msg = await channel.send(embed=embed, view=GiveawayStaffPanelView())
        data["staff_panel_message_id"] = msg.id
        save_json(GIVEAWAYS_FILE, data)

    elif kind == "ticket":
        data = load_json(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})
        message_id = data.get("panel_message_id")
        if message_id:
            msg = await safe_fetch_message(channel, message_id)
            if msg:
                return

        embed = discord.Embed(
            title="🎫 Support",
            description="Clique sur le bouton ci-dessous pour ouvrir un ticket privé.",
            color=discord.Color.green()
        )
        msg = await channel.send(embed=embed, view=TicketOpenView())
        data["panel_message_id"] = msg.id
        save_json(TICKETS_FILE, data)

    elif kind == "verify":
        data = load_json(VERIFY_FILE, {"users": {}, "panel_message_id": None})
        message_id = data.get("panel_message_id")
        if message_id:
            msg = await safe_fetch_message(channel, message_id)
            if msg:
                return

        embed = discord.Embed(
            title="🛡️ Vérification",
            description="Clique sur le bouton pour lancer la vérification captcha.",
            color=discord.Color.orange()
        )
        msg = await channel.send(embed=embed, view=VerifyPanelView())
        data["panel_message_id"] = msg.id
        save_json(VERIFY_FILE, data)

# =========================================================
# EVENTS
# =========================================================

@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return

    role = member.guild.get_role(UNVERIFIED_ROLE_ID)
    if role:
        try:
            await member.add_roles(role, reason="Nouveau membre non vérifié")
        except Exception:
            pass

    vdata = load_json(VERIFY_FILE, {"users": {}, "panel_message_id": None})
    vdata["users"][str(member.id)] = {
        "captcha": random_code(),
        "verified": False,
        "tries": 0,
        "created_at": dt_to_iso(now_utc()),
        "updated_at": dt_to_iso(now_utc())
    }
    save_json(VERIFY_FILE, vdata)

    embed = discord.Embed(
        title="📥 Nouveau membre",
        description=(
            f"{member.mention} a rejoint le serveur.\n\n"
            f"**Nom :** `{member}`\n"
            f"**ID :** `{member.id}`\n"
            f"**Compte créé :** <t:{int(member.created_at.timestamp())}:F>\n"
            f"**Membres totaux :** `{member.guild.member_count}`"
        ),
        color=discord.Color.green(),
        timestamp=now_utc()
    )

    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)

    await log_member_event(member.guild, embed)
    await update_server_stats_once()
    
@bot.event
async def on_member_remove(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return

    embed = discord.Embed(
        title="📤 Membre parti",
        description=(
            f"`{member}` a quitté le serveur.\n\n"
            f"**ID :** `{member.id}`\n"
            f"**Membres restants :** `{member.guild.member_count}`"
        ),
        color=discord.Color.red(),
        timestamp=now_utc()
    )

    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)

    await log_member_event(member.guild, embed)
    await update_server_stats_once()

@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user} ({bot.user.id})")

    bot.add_view(GiveawayStaffPanelView())
    bot.add_view(GiveawayJoinView())
    bot.add_view(GiveawayEndedView())
    bot.add_view(TicketOpenView())
    bot.add_view(TicketManageView())
    bot.add_view(VerifyPanelView())

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        print("Serveur introuvable. Vérifie GUILD_ID.")
        return

    giveaway_staff_channel = guild.get_channel(GIVEAWAY_STAFF_PANEL_CHANNEL_ID)
    ticket_channel = guild.get_channel(TICKET_PANEL_CHANNEL_ID)
    verify_channel = guild.get_channel(VERIFY_CHANNEL_ID)

    if isinstance(giveaway_staff_channel, discord.TextChannel):
        await ensure_panel(giveaway_staff_channel, "giveaway_staff")

    if isinstance(ticket_channel, discord.TextChannel):
        await ensure_panel(ticket_channel, "ticket")

    if isinstance(verify_channel, discord.TextChannel):
        await ensure_panel(verify_channel, "verify")

    if not giveaway_watcher.is_running():
        giveaway_watcher.start()

    if not verify_timeout_watcher.is_running():
        verify_timeout_watcher.start()

    if not update_server_stats_loop.is_running():
        update_server_stats_loop.start()

    await update_server_stats_once()

# =========================================================
# START
# =========================================================

bot.run(TOKEN)
