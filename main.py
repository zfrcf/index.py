import os
import io
import re
import json
import asyncio
import random
import string
import threading
from collections import defaultdict, deque
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from flask import Flask, request, abort

# =========================================================
# ENV
# =========================================================

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("La variable d'environnement TOKEN est introuvable.")

WEB_TOKEN = os.getenv("WEB_TOKEN", "")
PORT = int(os.getenv("PORT", "8080"))

# =========================================================
# DATA
# =========================================================

DATA_DIR = "bot_data"
os.makedirs(DATA_DIR, exist_ok=True)

GIVEAWAYS_FILE = os.path.join(DATA_DIR, "giveaways.json")
TICKETS_FILE = os.path.join(DATA_DIR, "tickets.json")
VERIFY_FILE = os.path.join(DATA_DIR, "verify.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist.json")
SECURITY_FILE = os.path.join(DATA_DIR, "security.json")

# =========================================================
# CONFIG
# =========================================================

GUILD_ID = 1336409517298286612

# Giveaways
GIVEAWAY_STAFF_PANEL_CHANNEL_ID = 1496178317739556994
GIVEAWAY_PUBLIC_CHANNEL_ID = 1496178317739556994
GIVEAWAY_ALLOWED_ROLE_ID = 1496179072005443644

# Tickets
TICKET_PANEL_CHANNEL_ID = 1496179371864621116
TICKET_CATEGORY_ID = 1496178590080040960
TICKET_STAFF_ROLE_ID = 1496179072005443644
TICKET_LOG_CHANNEL_ID = 1496178736217854002

# Verify
VERIFY_CHANNEL_ID = 1496179758969651390
UNVERIFIED_ROLE_ID = 1496179992651104506
VERIFIED_ROLE_ID = 1496179832717836368
VERIFY_TIMEOUT_SECONDS = 300
MAX_VERIFY_TRIES = 3
SEND_VERIFY_WELCOME_MESSAGE = True

# Logs
MEMBER_LOG_CHANNEL_ID = 1496554423491760218
VERIFY_LOG_CHANNEL_ID = 1496461263381856388
SANCTION_LOG_CHANNEL_ID = 1496564599707930755
SECURITY_LOG_CHANNEL_ID = 1496588030574858280

# Server stats
ALL_MEMBERS_CHANNEL_ID = 1496534691715743854
MEMBERS_CHANNEL_ID = 1496538997323862179
BOTS_CHANNEL_ID = 1496539148570591252

# Whitelist
STAFF_ROLE_IDS = [1496179072005443644]
PARTNER_ROLE_IDS = []

# Anti-alt
MIN_ACCOUNT_AGE_DAYS = 3
AUTO_KICK_TOO_RECENT_ACCOUNT = False

# Anti-double heuristique
ENABLE_ANTI_DOUBLE_HEURISTIC = True
ANTI_DOUBLE_LOOKBACK_DAYS = 14
AUTO_KICK_ON_DOUBLE_HEURISTIC = False

# Anti profil
ANTI_NO_AVATAR_ENABLED = True
AUTO_KICK_NO_AVATAR = False

ANTI_SUSPICIOUS_NAME_ENABLED = True
AUTO_KICK_SUSPICIOUS_NAME = False
SUSPICIOUS_NAME_PATTERNS = [
    r"free\s*nitro",
    r"steam\s*gift",
    r"admin\s*support",
    r"mod\s*support",
    r"discord\s*staff",
    r"claim\s*gift",
    r"gift\s*card",
    r"crypto",
    r"airdrop",
    r"http",
    r"www\.",
]

# Anti messages
ANTI_MASS_MENTION_ENABLED = True
ANTI_MASS_MENTION_THRESHOLD = 5
ANTI_LINK_SPAM_ENABLED = True
ANTI_LINK_SPAM_THRESHOLD = 3
ANTI_LINK_SPAM_WINDOW = 20
AUTO_TIMEOUT_ON_MASS_MENTION = True
AUTO_TIMEOUT_ON_LINK_SPAM = True
AUTO_TIMEOUT_DURATION_MINUTES = 30
DELETE_FLAGGED_MESSAGES = True

# Anti raid
AUTO_REBAN_BLACKLISTED = True
RAID_JOIN_THRESHOLD = 5
RAID_TIME_WINDOW_SECONDS = 15
RAID_MODE_DURATION = 300
RAID_KICK_RECENT_ACCOUNTS = True
RAID_MIN_ACCOUNT_AGE_DAYS = 7
RAID_LOCKDOWN_CHANNELS = True
RAID_DISABLE_INVITES = True

# =========================================================
# FILE INIT
# =========================================================

def ensure_file(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=4, ensure_ascii=False)

ensure_file(GIVEAWAYS_FILE, {"giveaways": {}, "staff_panel_message_id": None})
ensure_file(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})
ensure_file(VERIFY_FILE, {"users": {}, "panel_message_id": None})
ensure_file(BLACKLIST_FILE, {"banned_ids": []})
ensure_file(SECURITY_FILE, {
    "raid_mode": False,
    "raid_until": None,
    "locked_channels": [],
    "join_timestamps": []
})

# =========================================================
# UTILS
# =========================================================

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

def load_blacklist():
    return load_json(BLACKLIST_FILE, {"banned_ids": []})

def save_blacklist(data):
    save_json(BLACKLIST_FILE, data)

def load_security():
    return load_json(SECURITY_FILE, {
        "raid_mode": False,
        "raid_until": None,
        "locked_channels": [],
        "join_timestamps": []
    })

def save_security(data):
    save_json(SECURITY_FILE, data)

def is_blacklisted(user_id: int) -> bool:
    return user_id in load_blacklist()["banned_ids"]

def add_blacklist(user_id: int):
    data = load_blacklist()
    if user_id not in data["banned_ids"]:
        data["banned_ids"].append(user_id)
        save_blacklist(data)

def remove_blacklist(user_id: int):
    data = load_blacklist()
    if user_id in data["banned_ids"]:
        data["banned_ids"].remove(user_id)
        save_blacklist(data)

# =========================================================
# BOT
# =========================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

message_tracker = defaultdict(lambda: deque())

# =========================================================
# HELPERS
# =========================================================

def has_role(member: discord.Member, role_id: int) -> bool:
    return any(role.id == role_id for role in member.roles)

def has_any_role(member: discord.Member, role_ids: list[int]) -> bool:
    return any(role.id in role_ids for role in member.roles)

def is_whitelisted(member: discord.Member) -> bool:
    return (
        member.guild_permissions.administrator
        or has_any_role(member, STAFF_ROLE_IDS)
        or has_any_role(member, PARTNER_ROLE_IDS)
    )

def is_giveaway_staff(member: discord.Member) -> bool:
    return has_role(member, GIVEAWAY_ALLOWED_ROLE_ID) or member.guild_permissions.administrator

def is_ticket_staff(member: discord.Member) -> bool:
    return has_role(member, TICKET_STAFF_ROLE_ID) or member.guild_permissions.administrator

def is_verified(member: discord.Member) -> bool:
    return has_role(member, VERIFIED_ROLE_ID)

def account_age_days(member: discord.Member) -> int:
    return max(0, (now_utc() - member.created_at).days)

def is_recent_account(member: discord.Member) -> bool:
    return account_age_days(member) < MIN_ACCOUNT_AGE_DAYS

def has_custom_avatar(member: discord.Member) -> bool:
    return member.avatar is not None

def suspicious_name(member: discord.Member) -> tuple[bool, str]:
    if not ANTI_SUSPICIOUS_NAME_ENABLED:
        return False, ""
    combined = f"{member.name} {member.display_name}".lower()
    for pattern in SUSPICIOUS_NAME_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return True, pattern
    return False, ""

def anti_double_suspicion(member: discord.Member) -> tuple[bool, str]:
    if not ENABLE_ANTI_DOUBLE_HEURISTIC:
        return False, ""
    lookback = timedelta(days=ANTI_DOUBLE_LOOKBACK_DAYS)
    lowered_name = member.name.lower().strip()
    lowered_display = member.display_name.lower().strip()

    for other in member.guild.members:
        if other.id == member.id or other.bot:
            continue
        if other.name.lower().strip() == lowered_name:
            if other.joined_at and (now_utc() - other.joined_at) <= lookback:
                return True, f"Même username que {other} ({other.id})"
        if lowered_display and other.display_name.lower().strip() == lowered_display and is_recent_account(member):
            if other.joined_at and (now_utc() - other.joined_at) <= lookback:
                return True, f"Même display name que {other} ({other.id}) + compte récent"
    return False, ""

def contains_links(content: str) -> bool:
    return re.search(r"(https?://\S+|discord\.gg/\S+|www\.\S+)", content, re.IGNORECASE) is not None

def mass_mention_count(message: discord.Message) -> int:
    return len(message.mentions) + len(message.role_mentions)

async def safe_fetch_message(channel: discord.TextChannel, message_id: int):
    try:
        return await channel.fetch_message(message_id)
    except Exception:
        return None

async def log_embed(channel_id: int, embed: discord.Embed):
    channel = bot.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

async def log_member_event(guild: discord.Guild, embed: discord.Embed):
    await log_embed(MEMBER_LOG_CHANNEL_ID, embed)

async def log_verify_event(guild: discord.Guild, embed: discord.Embed):
    await log_embed(VERIFY_LOG_CHANNEL_ID, embed)

async def log_sanction(guild: discord.Guild, embed: discord.Embed):
    await log_embed(SANCTION_LOG_CHANNEL_ID, embed)

async def log_security(guild: discord.Guild, embed: discord.Embed):
    await log_embed(SECURITY_LOG_CHANNEL_ID, embed)

async def log_ticket_action(guild: discord.Guild, content: str):
    channel = guild.get_channel(TICKET_LOG_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(content)
        except Exception:
            pass

async def timeout_member(member: discord.Member, minutes: int, reason: str):
    until = now_utc() + timedelta(minutes=minutes)
    try:
        await member.edit(timed_out_until=until, reason=reason)
        return True
    except Exception:
        return False

# =========================================================
# CAPTCHA IMAGE
# =========================================================

def get_font(size: int):
    possible_fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in possible_fonts:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()

def generate_captcha_image(code: str) -> io.BytesIO:
    width, height = 340, 120
    image = Image.new("RGB", (width, height), (245, 247, 252))
    draw = ImageDraw.Draw(image)

    for _ in range(25):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = x1 + random.randint(20, 80)
        y2 = y1 + random.randint(5, 30)
        color = (random.randint(180, 230), random.randint(180, 230), random.randint(180, 230))
        draw.ellipse((x1, y1, x2, y2), outline=color, width=1)

    for _ in range(10):
        draw.line(
            (
                random.randint(0, width), random.randint(0, height),
                random.randint(0, width), random.randint(0, height),
            ),
            fill=(random.randint(70, 180), random.randint(70, 180), random.randint(70, 180)),
            width=random.randint(1, 3),
        )

    font = get_font(42)
    x = 25
    for char in code:
        y = random.randint(20, 45)
        color = (random.randint(20, 90), random.randint(20, 90), random.randint(20, 90))
        angle = random.randint(-20, 20)

        char_img = Image.new("RGBA", (60, 70), (255, 255, 255, 0))
        char_draw = ImageDraw.Draw(char_img)
        char_draw.text((10, 5), char, font=font, fill=color)
        char_img = char_img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=1)
        image.paste(char_img, (x, y), char_img)
        x += random.randint(42, 52)

    for _ in range(1200):
        draw.point(
            (random.randint(0, width - 1), random.randint(0, height - 1)),
            fill=(random.randint(120, 220), random.randint(120, 220), random.randint(120, 220)),
        )

    image = image.filter(ImageFilter.SMOOTH)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

def captcha_discord_file(code: str) -> discord.File:
    return discord.File(generate_captcha_image(code), filename="captcha.png")

# =========================================================
# RAID / LOCKDOWN
# =========================================================

def raid_mode_active() -> bool:
    data = load_security()
    if not data.get("raid_mode"):
        return False
    raid_until = data.get("raid_until")
    if not raid_until:
        return False
    try:
        until_dt = iso_to_dt(raid_until)
    except Exception:
        return False
    if now_utc() >= until_dt:
        data["raid_mode"] = False
        data["raid_until"] = None
        save_security(data)
        return False
    return True

async def lockdown_guild(guild: discord.Guild):
    security = load_security()
    if security.get("locked_channels"):
        return

    locked_channels = []
    for channel in guild.channels:
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel, discord.StageChannel)):
            try:
                everyone = channel.overwrites_for(guild.default_role)
                locked_channels.append({
                    "channel_id": channel.id,
                    "send_messages": getattr(everyone, "send_messages", None),
                    "add_reactions": getattr(everyone, "add_reactions", None),
                    "create_public_threads": getattr(everyone, "create_public_threads", None),
                    "create_private_threads": getattr(everyone, "create_private_threads", None),
                    "send_messages_in_threads": getattr(everyone, "send_messages_in_threads", None),
                    "create_instant_invite": getattr(everyone, "create_instant_invite", None),
                })
                everyone.send_messages = False
                if hasattr(everyone, "add_reactions"):
                    everyone.add_reactions = False
                if hasattr(everyone, "create_public_threads"):
                    everyone.create_public_threads = False
                if hasattr(everyone, "create_private_threads"):
                    everyone.create_private_threads = False
                if hasattr(everyone, "send_messages_in_threads"):
                    everyone.send_messages_in_threads = False
                if RAID_DISABLE_INVITES:
                    everyone.create_instant_invite = False
                await channel.set_permissions(guild.default_role, overwrite=everyone)
            except Exception:
                pass

    security["locked_channels"] = locked_channels
    save_security(security)

async def unlock_guild(guild: discord.Guild):
    security = load_security()
    for entry in security.get("locked_channels", []):
        channel = guild.get_channel(entry["channel_id"])
        if not channel:
            continue
        try:
            everyone = channel.overwrites_for(guild.default_role)
            everyone.send_messages = entry["send_messages"]
            if hasattr(everyone, "add_reactions"):
                everyone.add_reactions = entry["add_reactions"]
            if hasattr(everyone, "create_public_threads"):
                everyone.create_public_threads = entry["create_public_threads"]
            if hasattr(everyone, "create_private_threads"):
                everyone.create_private_threads = entry["create_private_threads"]
            if hasattr(everyone, "send_messages_in_threads"):
                everyone.send_messages_in_threads = entry["send_messages_in_threads"]
            everyone.create_instant_invite = entry["create_instant_invite"]
            await channel.set_permissions(guild.default_role, overwrite=everyone)
        except Exception:
            pass

    security["locked_channels"] = []
    save_security(security)

async def enable_raid_mode(guild: discord.Guild, reason: str):
    if raid_mode_active():
        return
    security = load_security()
    security["raid_mode"] = True
    security["raid_until"] = dt_to_iso(now_utc() + timedelta(seconds=RAID_MODE_DURATION))
    save_security(security)

    if RAID_LOCKDOWN_CHANNELS:
        await lockdown_guild(guild)

    embed = discord.Embed(
        title="🚨 Mode raid activé",
        description=(
            f"**Raison :** {reason}\n"
            f"**Durée :** {RAID_MODE_DURATION}s\n"
            f"**Lockdown :** {'Oui' if RAID_LOCKDOWN_CHANNELS else 'Non'}\n"
            f"**Blocage invites :** {'Oui' if RAID_DISABLE_INVITES else 'Non'}"
        ),
        color=discord.Color.red(),
        timestamp=now_utc()
    )
    await log_security(guild, embed)

async def disable_raid_mode(guild: discord.Guild):
    security = load_security()
    if not security.get("raid_mode"):
        return

    security["raid_mode"] = False
    security["raid_until"] = None
    save_security(security)

    if RAID_LOCKDOWN_CHANNELS:
        await unlock_guild(guild)

    embed = discord.Embed(
        title="✅ Mode raid désactivé",
        description="Le serveur est revenu en mode normal.",
        color=discord.Color.green(),
        timestamp=now_utc()
    )
    await log_security(guild, embed)

# =========================================================
# GIVEAWAYS
# =========================================================

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
            return await interaction.response.send_message("Les valeurs doivent être > 0.", ephemeral=True)

        public_channel = interaction.guild.get_channel(GIVEAWAY_PUBLIC_CHANNEL_ID)
        if not isinstance(public_channel, discord.TextChannel):
            return await interaction.response.send_message("Salon giveaways introuvable.", ephemeral=True)

        end_at = now_utc() + timedelta(minutes=duration)
        embed = discord.Embed(
            title="🎉 GIVEAWAY",
            description=(
                f"**Lot :** {self.prize}\n"
                f"**Gagnants :** {winners}\n"
                f"**Fin :** {ts_full(end_at)} ({ts_relative(end_at)})\n\n"
                f"Clique sur **Participer**."
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
        await interaction.response.send_message(f"Giveaway créé dans {public_channel.mention}", ephemeral=True)

class GiveawayStaffPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Créer un giveaway", emoji="🎉", style=discord.ButtonStyle.success, custom_id="giveaway_staff_create")
    async def create_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)
        if not is_giveaway_staff(interaction.user):
            return await interaction.response.send_message("Seuls les modérateurs peuvent créer un giveaway.", ephemeral=True)
        await interaction.response.send_modal(GiveawayCreateModal())

class GiveawayJoinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Participer", emoji="🎊", style=discord.ButtonStyle.primary, custom_id="giveaway_join_button")
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

    @discord.ui.button(label="Reroll", emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="giveaway_reroll_button")
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
        await interaction.channel.send(f"🔁 **Reroll** du giveaway **{giveaway['prize']}**\nNouveau(x) gagnant(s) : {mentions}")
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

    await channel.send(f"🎉 Giveaway terminé pour **{giveaway['prize']}**\nGagnant(s) : {winner_mentions}")

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

    @discord.ui.button(label="Ouvrir un ticket", emoji="🎫", style=discord.ButtonStyle.success, custom_id="ticket_open_button")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)
        if not is_verified(interaction.user):
            return await interaction.response.send_message("Tu dois être vérifié avant d'ouvrir un ticket.", ephemeral=True)

        guild = interaction.guild
        member = interaction.user
        tdata = load_json(TICKETS_FILE, {"tickets": {}, "panel_message_id": None})

        for channel_id, info in tdata["tickets"].items():
            if info["guild_id"] == guild.id and info["owner_id"] == member.id:
                existing = guild.get_channel(int(channel_id))
                if existing:
                    return await interaction.response.send_message(f"Tu as déjà un ticket ouvert : {existing.mention}", ephemeral=True)

        category = guild.get_channel(TICKET_CATEGORY_ID)
        staff_role = guild.get_role(TICKET_STAFF_ROLE_ID)
        if not isinstance(category, discord.CategoryChannel) or not staff_role:
            return await interaction.response.send_message("Configuration ticket invalide.", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
            staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
        }

        channel = await guild.create_text_channel(
            name=sanitize_channel_name(f"ticket-{member.name}"),
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
        await channel.send(content=f"{member.mention} <@&{TICKET_STAFF_ROLE_ID}>", embed=embed, view=TicketManageView())
        await log_ticket_action(guild, f"📂 Ticket créé : {channel.mention} par {member.mention}")
        await interaction.response.send_message(f"Ticket créé : {channel.mention}", ephemeral=True)

class AddUserModal(discord.ui.Modal, title="Ajouter un membre au ticket"):
    user_id_input = discord.ui.TextInput(label="ID utilisateur", placeholder="Colle l'ID Discord du membre", max_length=25)

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
    user_id_input = discord.ui.TextInput(label="ID utilisateur", placeholder="Colle l'ID Discord du membre", max_length=25)

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

        await log_ticket_action(interaction.guild, f"🗑️ Ticket fermé : #{interaction.channel.name} par {interaction.user.mention}")
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
        await interaction.response.send_message("Transcript généré.", file=discord.File(file_path), ephemeral=True)

    @discord.ui.button(label="Ajouter", emoji="➕", style=discord.ButtonStyle.primary, custom_id="ticket_add_user_button")
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)
        if not is_ticket_staff(interaction.user):
            return await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)
        await interaction.response.send_modal(AddUserModal(interaction.channel.id))

    @discord.ui.button(label="Retirer", emoji="➖", style=discord.ButtonStyle.secondary, custom_id="ticket_remove_user_button")
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Action invalide.", ephemeral=True)
        if not is_ticket_staff(interaction.user):
            return await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)
        await interaction.response.send_modal(RemoveUserModal(interaction.channel.id))

# =========================================================
# VERIFY
# =========================================================

class VerifyRetryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Entrer le code", emoji="✍️", style=discord.ButtonStyle.primary)
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyCaptchaModal())

class VerifyCaptchaModal(discord.ui.Modal, title="Validation du captcha"):
    captcha_input = discord.ui.TextInput(label="Recopie le captcha", placeholder="Exemple : A1B2C3", max_length=12)

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

            fail_embed = discord.Embed(
                title="❌ Vérification ratée",
                description=(
                    f"**Membre :** {interaction.user.mention}\n"
                    f"**Essais utilisés :** {entry['tries']}/{MAX_VERIFY_TRIES}\n"
                    f"**Essais restants :** {tries_left}"
                ),
                color=discord.Color.red(),
                timestamp=now_utc()
            )
            if interaction.user.display_avatar:
                fail_embed.set_thumbnail(url=interaction.user.display_avatar.url)
            await log_verify_event(interaction.guild, fail_embed)

            if tries_left <= 0:
                try:
                    await interaction.guild.kick(interaction.user, reason="Captcha incorrect trop de fois")
                except Exception:
                    pass

                kick_embed = discord.Embed(
                    title="🛑 Expulsion automatique",
                    description=(
                        f"**Membre :** `{interaction.user}`\n"
                        f"**ID :** `{interaction.user.id}`\n"
                        f"**Raison :** Trop d'erreurs captcha"
                    ),
                    color=discord.Color.dark_red(),
                    timestamp=now_utc()
                )
                await log_verify_event(interaction.guild, kick_embed)

                return await interaction.response.send_message("❌ Trop d'erreurs. Tu as été expulsé.", ephemeral=True)

            image_file = captcha_discord_file(entry["captcha"])
            embed = discord.Embed(
                title="❌ Captcha incorrect",
                description=f"Il te reste **{tries_left} essai(s)**.\nUn nouveau captcha a été généré.",
                color=discord.Color.red()
            )
            embed.set_image(url="attachment://captcha.png")
            return await interaction.response.send_message(embed=embed, file=image_file, ephemeral=True, view=VerifyRetryView())

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

        success_embed = discord.Embed(
            title="✅ Vérification réussie",
            description=f"**Membre :** {interaction.user.mention}\nLe membre a obtenu l'accès au serveur.",
            color=discord.Color.green(),
            timestamp=now_utc()
        )
        if interaction.user.display_avatar:
            success_embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await log_verify_event(interaction.guild, success_embed)

        done_embed = discord.Embed(
            title="✅ Vérification réussie",
            description="Tu as maintenant accès au serveur.",
            color=discord.Color.green()
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
            "updated_at": dt_to_iso(now_utc())
        }
        save_json(VERIFY_FILE, vdata)

        image_file = captcha_discord_file(code)
        embed = discord.Embed(
            title=f"Bienvenue sur {interaction.guild.name}",
            description=(
                "Pour accéder au serveur, complète le captcha.\n\n"
                f"**Temps limite :** 5 minutes\n"
                f"**Essais max :** {MAX_VERIFY_TRIES}\n"
                f"**Âge minimal conseillé du compte :** {MIN_ACCOUNT_AGE_DAYS} jour(s)"
            ),
            color=discord.Color.orange()
        )
        embed.set_image(url="attachment://captcha.png")
        embed.set_footer(text="Clique sur Entrer le code pour valider.")
        await interaction.response.send_message(embed=embed, file=image_file, ephemeral=True, view=VerifyRetryView())

@tasks.loop(seconds=30)
async def verify_timeout_watcher():
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

                kick_embed = discord.Embed(
                    title="🛑 Expulsion automatique",
                    description=(
                        f"**Membre :** `{member}`\n"
                        f"**ID :** `{member.id}`\n"
                        f"**Raison :** Non vérifié après 5 minutes"
                    ),
                    color=discord.Color.dark_red(),
                    timestamp=now_utc()
                )
                await log_verify_event(guild, kick_embed)

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
            name = f"📊 All Members : {all_members}"
            if all_channel.name != name:
                await all_channel.edit(name=name)

        if members_channel and MEMBERS_CHANNEL_ID != 0:
            name = f"👥 Members : {humans}"
            if members_channel.name != name:
                await members_channel.edit(name=name)

        if bots_channel and BOTS_CHANNEL_ID != 0:
            name = f"🤖 Bots : {bots_count}"
            if bots_channel.name != name:
                await bots_channel.edit(name=name)
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
# WELCOME
# =========================================================

async def send_verify_welcome(member: discord.Member):
    if not SEND_VERIFY_WELCOME_MESSAGE:
        return
    channel = member.guild.get_channel(VERIFY_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return

    embed = discord.Embed(
        title=f"Bienvenue sur {member.guild.name}",
        description=(
            f"{member.mention}, pour accéder au serveur, tu dois compléter la vérification.\n\n"
            f"🔒 Tu ne verras le reste du serveur qu’après validation.\n"
            f"⏳ Tu as **5 minutes** pour te vérifier.\n"
            f"❌ Après **{MAX_VERIFY_TRIES} erreurs**, tu peux être expulsé."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc()
    )
    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Clique sur le panneau de vérification ci-dessous.")
    try:
        await channel.send(embed=embed)
    except Exception:
        pass

# =========================================================
# SANCTION LOGS
# =========================================================

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    embed = discord.Embed(
        title="🔨 Membre banni",
        description=f"**Utilisateur :** `{user}`\n**ID :** `{user.id}`",
        color=discord.Color.dark_red(),
        timestamp=now_utc()
    )
    try:
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
            if entry.target.id == user.id:
                embed.add_field(name="Modérateur", value=entry.user.mention, inline=True)
                embed.add_field(name="Raison", value=entry.reason or "Aucune", inline=True)
                break
    except Exception:
        pass
    await log_sanction(guild, embed)

@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    embed = discord.Embed(
        title="✅ Membre débanni",
        description=f"**Utilisateur :** `{user}`\n**ID :** `{user.id}`",
        color=discord.Color.green(),
        timestamp=now_utc()
    )
    try:
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.unban):
            if entry.target.id == user.id:
                embed.add_field(name="Modérateur", value=entry.user.mention, inline=True)
                embed.add_field(name="Raison", value=entry.reason or "Aucune", inline=True)
                break
    except Exception:
        pass
    await log_sanction(guild, embed)

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.guild.id != GUILD_ID:
        return

    before_timeout = before.timed_out_until
    after_timeout = after.timed_out_until

    if before_timeout != after_timeout and after_timeout is not None:
        embed = discord.Embed(
            title="🔇 Membre timeout",
            description=(
                f"**Membre :** {after.mention}\n"
                f"**ID :** `{after.id}`\n"
                f"**Fin du timeout :** <t:{int(after_timeout.timestamp())}:F>"
            ),
            color=discord.Color.orange(),
            timestamp=now_utc()
        )
        try:
            async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.member_update):
                if entry.target.id == after.id:
                    embed.add_field(name="Modérateur", value=entry.user.mention, inline=True)
                    embed.add_field(name="Raison", value=entry.reason or "Aucune", inline=True)
                    break
        except Exception:
            pass
        await log_sanction(after.guild, embed)

    if before_timeout is not None and after_timeout is None:
        embed = discord.Embed(
            title="🔊 Timeout retiré",
            description=f"**Membre :** {after.mention}\n**ID :** `{after.id}`",
            color=discord.Color.green(),
            timestamp=now_utc()
        )
        try:
            async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.member_update):
                if entry.target.id == after.id:
                    embed.add_field(name="Modérateur", value=entry.user.mention, inline=True)
                    embed.add_field(name="Raison", value=entry.reason or "Aucune", inline=True)
                    break
        except Exception:
            pass
        await log_sanction(after.guild, embed)

# =========================================================
# COMMANDS
# =========================================================

@bot.command()
@commands.has_permissions(ban_members=True)
async def banid(ctx, user_id: int, *, reason="Aucune raison"):
    guild = ctx.guild
    if guild is None:
        return
    try:
        user = await bot.fetch_user(user_id)
        await guild.ban(user, reason=f"{reason} | Ban ID par {ctx.author}")
        add_blacklist(user_id)
        embed = discord.Embed(
            title="🔨 Ban ID effectué",
            description=(
                f"**Utilisateur :** `{user}`\n"
                f"**ID :** `{user_id}`\n"
                f"**Modérateur :** {ctx.author.mention}\n"
                f"**Raison :** {reason}"
            ),
            color=discord.Color.dark_red(),
            timestamp=now_utc()
        )
        await log_security(guild, embed)
        await ctx.send(f"✅ ID `{user_id}` banni et blacklisté.")
    except Exception as e:
        await ctx.send(f"❌ Impossible de bannir cet ID : `{e}`")

@bot.command()
@commands.has_permissions(ban_members=True)
async def unbanid(ctx, user_id: int):
    guild = ctx.guild
    if guild is None:
        return
    try:
        user = await bot.fetch_user(user_id)
        await guild.unban(user, reason=f"Unban ID par {ctx.author}")
        remove_blacklist(user_id)
        embed = discord.Embed(
            title="✅ Unban ID effectué",
            description=(
                f"**Utilisateur :** `{user}`\n"
                f"**ID :** `{user_id}`\n"
                f"**Modérateur :** {ctx.author.mention}"
            ),
            color=discord.Color.green(),
            timestamp=now_utc()
        )
        await log_security(guild, embed)
        await ctx.send(f"✅ ID `{user_id}` débanni et retiré de la blacklist.")
    except Exception as e:
        await ctx.send(f"❌ Impossible de débannir cet ID : `{e}`")

@bot.command()
@commands.has_permissions(administrator=True)
async def raid(ctx, mode: str):
    mode = mode.lower().strip()
    if mode == "on":
        await enable_raid_mode(ctx.guild, f"Activation manuelle par {ctx.author}")
        await ctx.send("🚨 Mode raid activé.")
    elif mode == "off":
        await disable_raid_mode(ctx.guild)
        await ctx.send("✅ Mode raid désactivé.")
    else:
        await ctx.send("Utilise `!raid on` ou `!raid off`.")

# =========================================================
# EVENTS
# =========================================================

DELETE_MESSAGE_CHOICES = [
    app_commands.Choice(name="Don't Delete Any", value=0),
    app_commands.Choice(name="Previous Hour", value=3600),
    app_commands.Choice(name="Previous 6 Hours", value=21600),
    app_commands.Choice(name="Previous 12 Hours", value=43200),
    app_commands.Choice(name="Previous 24 Hours", value=86400),
    app_commands.Choice(name="Previous 3 Days", value=259200),
    app_commands.Choice(name="Previous 7 Days", value=604800),
]

@bot.tree.command(name="ban", description="Bannir un membre du serveur")
@app_commands.describe(
    user="Membre à bannir",
    delete_messages="Messages à supprimer",
    reason="Raison du bannissement"
)
@app_commands.choices(delete_messages=DELETE_MESSAGE_CHOICES)
async def slash_ban(
    interaction: discord.Interaction,
    user: discord.Member,
    delete_messages: app_commands.Choice[int],
    reason: str
):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Action invalide.", ephemeral=True)

    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message(
            "Tu n'as pas la permission de bannir.",
            ephemeral=True
        )

    try:
        await interaction.guild.ban(
            user,
            delete_message_seconds=delete_messages.value,
            reason=f"{reason} | par {interaction.user}"
        )
        add_blacklist(user.id)

        embed = discord.Embed(
            title="🔨 Bannissement effectué",
            description=(
                f"**Membre :** {user.mention}\n"
                f"**ID :** `{user.id}`\n"
                f"**Modérateur :** {interaction.user.mention}\n"
                f"**Suppression messages :** `{delete_messages.name}`\n"
                f"**Raison :** {reason}"
            ),
            color=discord.Color.dark_red(),
            timestamp=now_utc()
        )

        await log_sanction(interaction.guild, embed)
        await log_security(interaction.guild, embed)

        await interaction.response.send_message(
            f"✅ {user.mention} a été banni.",
            ephemeral=True
        )

    except Exception as e:
        await interaction.response.send_message(
            f"❌ Impossible de bannir ce membre : `{e}`",
            ephemeral=True
        )

@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return

    if AUTO_REBAN_BLACKLISTED and is_blacklisted(member.id):
        try:
            await member.guild.ban(member, reason="ID blacklisté")
        except Exception:
            pass
        embed = discord.Embed(
            title="⛔ Membre blacklisté détecté",
            description=(
                f"**Membre :** `{member}`\n"
                f"**ID :** `{member.id}`\n"
                f"**Action :** Ban automatique"
            ),
            color=discord.Color.dark_red(),
            timestamp=now_utc()
        )
        await log_security(member.guild, embed)
        return

    security = load_security()
    timestamps = security.get("join_timestamps", [])
    current_ts = now_utc().timestamp()
    timestamps = [ts for ts in timestamps if current_ts - ts <= RAID_TIME_WINDOW_SECONDS]
    timestamps.append(current_ts)
    security["join_timestamps"] = timestamps
    save_security(security)

    if len(timestamps) >= RAID_JOIN_THRESHOLD:
        await enable_raid_mode(member.guild, f"{len(timestamps)} arrivées en moins de {RAID_TIME_WINDOW_SECONDS}s")

    if raid_mode_active() and RAID_KICK_RECENT_ACCOUNTS and not is_whitelisted(member):
        if account_age_days(member) < RAID_MIN_ACCOUNT_AGE_DAYS:
            try:
                await member.guild.kick(member, reason="Compte trop récent pendant un raid")
            except Exception:
                pass
            embed = discord.Embed(
                title="🛡️ Protection anti-raid",
                description=(
                    f"**Membre :** `{member}`\n"
                    f"**ID :** `{member.id}`\n"
                    f"**Âge du compte :** `{account_age_days(member)} jour(s)`\n"
                    f"**Action :** Kick auto"
                ),
                color=discord.Color.orange(),
                timestamp=now_utc()
            )
            await log_security(member.guild, embed)
            return

    if ANTI_NO_AVATAR_ENABLED and not has_custom_avatar(member) and not is_whitelisted(member):
        embed = discord.Embed(
            title="⚠️ Compte sans avatar détecté",
            description=f"**Membre :** {member.mention}\n**ID :** `{member.id}`",
            color=discord.Color.orange(),
            timestamp=now_utc()
        )
        await log_security(member.guild, embed)
        if AUTO_KICK_NO_AVATAR:
            try:
                await member.guild.kick(member, reason="Compte sans avatar")
            except Exception:
                pass
            return

    suspicious, pattern = suspicious_name(member)
    if suspicious and not is_whitelisted(member):
        embed = discord.Embed(
            title="⚠️ Pseudo suspect détecté",
            description=f"**Membre :** {member.mention}\n**ID :** `{member.id}`\n**Pattern :** `{pattern}`",
            color=discord.Color.orange(),
            timestamp=now_utc()
        )
        await log_security(member.guild, embed)
        if AUTO_KICK_SUSPICIOUS_NAME:
            try:
                await member.guild.kick(member, reason=f"Pseudo suspect: {pattern}")
            except Exception:
                pass
            return

    if is_recent_account(member) and not is_whitelisted(member):
        embed = discord.Embed(
            title="⚠️ Compte récent détecté",
            description=(
                f"**Membre :** {member.mention}\n"
                f"**Âge du compte :** `{account_age_days(member)} jour(s)`\n"
                f"**Minimum attendu :** `{MIN_ACCOUNT_AGE_DAYS} jour(s)`"
            ),
            color=discord.Color.orange(),
            timestamp=now_utc()
        )
        await log_verify_event(member.guild, embed)
        if AUTO_KICK_TOO_RECENT_ACCOUNT:
            try:
                await member.guild.kick(member, reason="Compte trop récent")
            except Exception:
                pass
            return

    suspicious_double, reason = anti_double_suspicion(member)
    if suspicious_double and not is_whitelisted(member):
        embed = discord.Embed(
            title="⚠️ Suspicion de double compte",
            description=f"**Membre :** {member.mention}\n**Raison :** {reason}",
            color=discord.Color.orange(),
            timestamp=now_utc()
        )
        await log_verify_event(member.guild, embed)
        if AUTO_KICK_ON_DOUBLE_HEURISTIC:
            try:
                await member.guild.kick(member, reason=f"Suspicion double compte: {reason}")
            except Exception:
                pass
            return

    unverified_role = member.guild.get_role(UNVERIFIED_ROLE_ID)
    if unverified_role:
        try:
            await member.add_roles(unverified_role, reason="Nouveau membre non vérifié")
        except Exception as e:
            print("Erreur ajout rôle UNVERIFIED :", e)

    vdata = load_json(VERIFY_FILE, {"users": {}, "panel_message_id": None})
    vdata["users"][str(member.id)] = {
        "captcha": random_code(),
        "verified": False,
        "tries": 0,
        "created_at": dt_to_iso(now_utc()),
        "updated_at": dt_to_iso(now_utc())
    }
    save_json(VERIFY_FILE, vdata)

    await send_verify_welcome(member)

    join_embed = discord.Embed(
        title="📥 Nouveau membre",
        description=(
            f"{member.mention} a rejoint le serveur.\n\n"
            f"**Nom :** `{member}`\n"
            f"**ID :** `{member.id}`\n"
            f"**Compte créé :** <t:{int(member.created_at.timestamp())}:F>\n"
            f"**Âge du compte :** `{account_age_days(member)} jour(s)`\n"
            f"**Membres totaux :** `{member.guild.member_count}`"
        ),
        color=discord.Color.green(),
        timestamp=now_utc()
    )
    if member.display_avatar:
        join_embed.set_thumbnail(url=member.display_avatar.url)
    await log_member_event(member.guild, join_embed)

    await update_server_stats_once()

@bot.event
async def on_member_remove(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return

    kicked = False
    moderator = None
    reason = None
    try:
        async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
            if entry.target.id == member.id:
                kicked = True
                moderator = entry.user
                reason = entry.reason
                break
    except Exception:
        pass

    if kicked:
        sanction_embed = discord.Embed(
            title="👢 Membre expulsé",
            description=f"**Utilisateur :** `{member}`\n**ID :** `{member.id}`",
            color=discord.Color.orange(),
            timestamp=now_utc()
        )
        if moderator:
            sanction_embed.add_field(name="Modérateur", value=moderator.mention, inline=True)
        sanction_embed.add_field(name="Raison", value=reason or "Aucune", inline=True)
        await log_sanction(member.guild, sanction_embed)

    leave_embed = discord.Embed(
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
        leave_embed.set_thumbnail(url=member.display_avatar.url)
    await log_member_event(member.guild, leave_embed)

    await update_server_stats_once()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if message.guild.id != GUILD_ID:
        return
    if not isinstance(message.author, discord.Member):
        return

    if is_whitelisted(message.author):
        await bot.process_commands(message)
        return

    if ANTI_MASS_MENTION_ENABLED:
        mention_count = mass_mention_count(message)
        if mention_count >= ANTI_MASS_MENTION_THRESHOLD:
            if DELETE_FLAGGED_MESSAGES:
                try:
                    await message.delete()
                except Exception:
                    pass

            embed = discord.Embed(
                title="🚨 Mass mention détecté",
                description=(
                    f"**Auteur :** {message.author.mention}\n"
                    f"**Mentions :** `{mention_count}`\n"
                    f"**Salon :** {message.channel.mention}\n"
                    f"**Contenu :**\n```{message.content[:800]}```"
                ),
                color=discord.Color.red(),
                timestamp=now_utc()
            )
            await log_security(message.guild, embed)

            if AUTO_TIMEOUT_ON_MASS_MENTION:
                success = await timeout_member(message.author, AUTO_TIMEOUT_DURATION_MINUTES, f"Mass mention ({mention_count})")
                if success:
                    sanction_embed = discord.Embed(
                        title="🔇 Timeout automatique",
                        description=(
                            f"**Membre :** {message.author.mention}\n"
                            f"**Raison :** Mass mention\n"
                            f"**Durée :** `{AUTO_TIMEOUT_DURATION_MINUTES} min`"
                        ),
                        color=discord.Color.orange(),
                        timestamp=now_utc()
                    )
                    await log_security(message.guild, sanction_embed)
            return

    if ANTI_LINK_SPAM_ENABLED and contains_links(message.content):
        key = (message.guild.id, message.author.id)
        now_ts = now_utc().timestamp()
        tracker = message_tracker[key]

        while tracker and now_ts - tracker[0] > ANTI_LINK_SPAM_WINDOW:
            tracker.popleft()
        tracker.append(now_ts)

        if len(tracker) >= ANTI_LINK_SPAM_THRESHOLD:
            if DELETE_FLAGGED_MESSAGES:
                try:
                    await message.delete()
                except Exception:
                    pass

            embed = discord.Embed(
                title="🚨 Spam de liens détecté",
                description=(
                    f"**Auteur :** {message.author.mention}\n"
                    f"**Liens récents :** `{len(tracker)}` en `{ANTI_LINK_SPAM_WINDOW}s`\n"
                    f"**Salon :** {message.channel.mention}\n"
                    f"**Contenu :**\n```{message.content[:800]}```"
                ),
                color=discord.Color.red(),
                timestamp=now_utc()
            )
            await log_security(message.guild, embed)

            if AUTO_TIMEOUT_ON_LINK_SPAM:
                success = await timeout_member(
                    message.author,
                    AUTO_TIMEOUT_DURATION_MINUTES,
                    f"Spam de liens ({len(tracker)} en {ANTI_LINK_SPAM_WINDOW}s)"
                )
                if success:
                    sanction_embed = discord.Embed(
                        title="🔇 Timeout automatique",
                        description=(
                            f"**Membre :** {message.author.mention}\n"
                            f"**Raison :** Spam de liens\n"
                            f"**Durée :** `{AUTO_TIMEOUT_DURATION_MINUTES} min`"
                        ),
                        color=discord.Color.orange(),
                        timestamp=now_utc()
                    )
                    await log_security(message.guild, sanction_embed)

            tracker.clear()
            return

    await bot.process_commands(message)

# =========================================================
# WEB INTERFACE
# =========================================================

app = Flask(__name__)

    async def fetch_bans_payload():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return {
            "guild_found": False,
            "bans": [],
            "blacklist_ids": load_blacklist()["banned_ids"]
        }

    bans = []
    try:
        async for ban_entry in guild.bans(limit=None):
            bans.append({
                "id": ban_entry.user.id,
                "name": str(ban_entry.user),
                "reason": ban_entry.reason or ""
            })
    except Exception:
        pass

    bans.sort(key=lambda x: str(x["name"]).lower())

    return {
        "guild_found": True,
        "bans": bans,
        "blacklist_ids": load_blacklist()["banned_ids"]
    }
@app.route("/")
def home():
    if not auth_ok():
        abort(403)
    return "<h2>Xerax bot web panel</h2><p>Routes: /bans</p>"

@app.route("/bans")
def bans_page():
    if not auth_ok():
        abort(403)

    try:
        future = asyncio.run_coroutine_threadsafe(fetch_bans_payload(), bot.loop)
        payload = future.result(timeout=10)
    except FuturesTimeoutError:
        return "<h3>Timeout lors de la récupération des bans.</h3>", 504
    except Exception as e:
        return f"<h3>Erreur : {e}</h3>", 500

    if not payload["guild_found"]:
        return "<h3>Serveur introuvable.</h3>", 404

    rows = []
    for b in payload["bans"]:
        rows.append(
            f"""
            <tr>
                <td>{b['id']}</td>
                <td>{b['name']}</td>
                <td>{b['reason'] or 'Aucune'}</td>
            </tr>
            """
        )

    bans_html = "".join(rows) or "<tr><td colspan='3'>Aucun banni.</td></tr>"
    blacklist_html = "".join(f"<li>{uid}</li>" for uid in payload["blacklist_ids"]) or "<li>Aucun ID blacklisté.</li>"

    html = f"""
    <html>
    <head>
        <title>Xerax - Bannissements</title>
        <style>
            body {{
                margin: 0;
                font-family: Arial, sans-serif;
                background: #0f172a;
                color: #e5e7eb;
                padding: 30px;
            }}
            .container {{
                max-width: 1100px;
                margin: 0 auto;
            }}
            .card {{
                background: #111827;
                border: 1px solid #1f2937;
                border-radius: 16px;
                padding: 20px;
                margin-bottom: 24px;
                box-shadow: 0 8px 24px rgba(0,0,0,.25);
            }}
            h1, h2 {{
                margin-top: 0;
            }}
            .muted {{
                color: #9ca3af;
                margin-bottom: 20px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            th, td {{
                padding: 12px;
                border-bottom: 1px solid #1f2937;
                text-align: left;
            }}
            th {{
                color: #93c5fd;
            }}
            code {{
                background: #1f2937;
                padding: 2px 6px;
                border-radius: 6px;
            }}
            ul {{
                padding-left: 20px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <h1>Liste des bannis</h1>
                <div class="muted">Serveur Discord : bannissements actifs</div>
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Nom</th>
                            <th>Raison</th>
                        </tr>
                    </thead>
                    <tbody>
                        {bans_html}
                    </tbody>
                </table>
            </div>

            <div class="card">
                <h2>Blacklist IDs</h2>
                <div class="muted">Utilisateurs à re-ban automatiquement s’ils reviennent</div>
                <ul>
                    {blacklist_html}
                </ul>
            </div>
        </div>
    </body>
    </html>
    """
    return html
# =========================================================
# TASKS
# =========================================================

@tasks.loop(seconds=15)
async def raid_mode_watcher():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    if not raid_mode_active():
        return

    security = load_security()
    raid_until = security.get("raid_until")
    if not raid_until:
        return

    try:
        until_dt = iso_to_dt(raid_until)
    except Exception:
        return

    if now_utc() >= until_dt:
        await disable_raid_mode(guild)

# =========================================================
# READY
# =========================================================

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
    if not raid_mode_watcher.is_running():
        raid_mode_watcher.start()

    await update_server_stats_once()

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    bot.run(TOKEN)
