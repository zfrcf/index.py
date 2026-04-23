from collections import defaultdict, deque

import discord
from discord.ext import tasks

from config import (
    GUILD_ID,
    MEMBER_LOG_CHANNEL_ID,
    VERIFY_LOG_CHANNEL_ID,
    SANCTION_LOG_CHANNEL_ID,
    SECURITY_LOG_CHANNEL_ID,
    VERIFY_FILE,
    BLACKLIST_FILE,
    SECURITY_FILE,
    VERIFY_TIMEOUT_SECONDS,
    UNVERIFIED_ROLE_ID,
    RAID_JOIN_THRESHOLD,
    RAID_TIME_WINDOW_SECONDS,
    RAID_MODE_DURATION,
    RAID_KICK_RECENT_ACCOUNTS,
    RAID_MIN_ACCOUNT_AGE_DAYS,
    RAID_LOCKDOWN_CHANNELS,
    RAID_DISABLE_INVITES,
    AUTO_REBAN_BLACKLISTED,
    ANTI_NO_AVATAR_ENABLED,
    AUTO_KICK_NO_AVATAR,
    AUTO_KICK_SUSPICIOUS_NAME,
    AUTO_KICK_TOO_RECENT_ACCOUNT,
    AUTO_KICK_ON_DOUBLE_HEURISTIC,
    ANTI_MASS_MENTION_ENABLED,
    ANTI_MASS_MENTION_THRESHOLD,
    ANTI_LINK_SPAM_ENABLED,
    ANTI_LINK_SPAM_THRESHOLD,
    ANTI_LINK_SPAM_WINDOW,
    AUTO_TIMEOUT_ON_MASS_MENTION,
    AUTO_TIMEOUT_ON_LINK_SPAM,
    AUTO_TIMEOUT_DURATION_MINUTES,
    DELETE_FLAGGED_MESSAGES,
    ALL_MEMBERS_CHANNEL_ID,
    MEMBERS_CHANNEL_ID,
    BOTS_CHANNEL_ID,
)
from storage import load_json, save_json
from utils import (
    now_utc,
    dt_to_iso,
    iso_to_dt,
    random_code,
    account_age_days,
    is_recent_account,
    has_custom_avatar,
    suspicious_name,
    anti_double_suspicion,
    is_whitelisted,
    contains_links,
    mass_mention_count,
)
from bot_views import ensure_panels, finish_giveaway

message_tracker = defaultdict(lambda: deque())


async def _send_embed(bot, channel_id: int, embed: discord.Embed):
    if channel_id == 0:
        return
    channel = bot.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(embed=embed)
        except Exception:
            pass


async def log_member_event(bot, guild: discord.Guild, embed: discord.Embed):
    await _send_embed(bot, MEMBER_LOG_CHANNEL_ID, embed)


async def log_verify_event(bot, guild: discord.Guild, embed: discord.Embed):
    await _send_embed(bot, VERIFY_LOG_CHANNEL_ID, embed)


async def log_sanction(bot, guild: discord.Guild, embed: discord.Embed):
    await _send_embed(bot, SANCTION_LOG_CHANNEL_ID, embed)


async def log_security(bot, guild: discord.Guild, embed: discord.Embed):
    await _send_embed(bot, SECURITY_LOG_CHANNEL_ID, embed)


def load_blacklist():
    return load_json(BLACKLIST_FILE, {"banned_ids": []})


def save_blacklist(data):
    save_json(BLACKLIST_FILE, data)


def is_blacklisted(user_id: int) -> bool:
    return user_id in load_blacklist()["banned_ids"]


def load_security():
    return load_json(
        SECURITY_FILE,
        {
            "raid_mode": False,
            "raid_until": None,
            "locked_channels": [],
            "join_timestamps": [],
        },
    )


def save_security(data):
    save_json(SECURITY_FILE, data)


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


async def timeout_member(member: discord.Member, minutes: int, reason: str):
    until = now_utc() + discord.utils.utcnow().replace(tzinfo=None) - discord.utils.utcnow().replace(tzinfo=None)
    # on reconstruit proprement en UTC
    until = now_utc() + __import__("datetime").timedelta(minutes=minutes)
    try:
        await member.edit(timed_out_until=until, reason=reason)
        return True
    except Exception:
        return False


async def lockdown_guild(guild: discord.Guild):
    security = load_security()
    if security.get("locked_channels"):
        return

    locked_channels = []

    for channel in guild.channels:
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel, discord.StageChannel)):
            try:
                everyone = channel.overwrites_for(guild.default_role)
                locked_channels.append(
                    {
                        "channel_id": channel.id,
                        "send_messages": getattr(everyone, "send_messages", None),
                        "add_reactions": getattr(everyone, "add_reactions", None),
                        "create_public_threads": getattr(everyone, "create_public_threads", None),
                        "create_private_threads": getattr(everyone, "create_private_threads", None),
                        "send_messages_in_threads": getattr(everyone, "send_messages_in_threads", None),
                        "create_instant_invite": getattr(everyone, "create_instant_invite", None),
                    }
                )

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
        if channel is None:
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


async def enable_raid_mode(bot, guild: discord.Guild, reason: str):
    if raid_mode_active():
        return

    security = load_security()
    security["raid_mode"] = True
    security["raid_until"] = dt_to_iso(now_utc() + __import__("datetime").timedelta(seconds=RAID_MODE_DURATION))
    save_security(security)

    if RAID_LOCKDOWN_CHANNELS:
        await lockdown_guild(guild)

    embed = discord.Embed(
        title="🚨 Mode raid activé",
        description=(
            f"**Raison :** {reason}\n"
            f"**Durée :** {RAID_MODE_DURATION} secondes\n"
            f"**Lockdown :** {'Oui' if RAID_LOCKDOWN_CHANNELS else 'Non'}\n"
            f"**Invites bloquées :** {'Oui' if RAID_DISABLE_INVITES else 'Non'}"
        ),
        color=discord.Color.red(),
        timestamp=now_utc(),
    )
    await log_security(bot, guild, embed)


async def disable_raid_mode(bot, guild: discord.Guild):
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
        timestamp=now_utc(),
    )
    await log_security(bot, guild, embed)


async def update_server_stats_once(bot):
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
    except Exception:
        pass


def setup_events(bot):
    @bot.event
    async def on_ready():
        print(f"Connecté en tant que {bot.user} ({bot.user.id})")
        await ensure_panels(bot)

       try:
    guild_obj = discord.Object(id=GUILD_ID)
    synced = await bot.tree.sync(guild=guild_obj)
    print("Slash synchronisées :", [cmd.name for cmd in synced])
except Exception as e:
    print("Erreur sync slash commands :", e)

        if not giveaway_watcher.is_running():
            giveaway_watcher.start()

        if not verify_timeout_watcher.is_running():
            verify_timeout_watcher.start()

        if not update_server_stats_loop.is_running():
            update_server_stats_loop.start()

        if not raid_mode_watcher.is_running():
            raid_mode_watcher.start()

        await update_server_stats_once(bot)

    @bot.event
    async def on_member_ban(guild: discord.Guild, user: discord.User):
        embed = discord.Embed(
            title="🔨 Membre banni",
            description=f"**Utilisateur :** `{user}`\n**ID :** `{user.id}`",
            color=discord.Color.dark_red(),
            timestamp=now_utc(),
        )
        await log_sanction(bot, guild, embed)

    @bot.event
    async def on_member_unban(guild: discord.Guild, user: discord.User):
        embed = discord.Embed(
            title="✅ Membre débanni",
            description=f"**Utilisateur :** `{user}`\n**ID :** `{user.id}`",
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        await log_sanction(bot, guild, embed)

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
                    f"**Fin :** <t:{int(after_timeout.timestamp())}:F>"
                ),
                color=discord.Color.orange(),
                timestamp=now_utc(),
            )
            await log_sanction(bot, after.guild, embed)

        if before_timeout is not None and after_timeout is None:
            embed = discord.Embed(
                title="🔊 Timeout retiré",
                description=f"**Membre :** {after.mention}\n**ID :** `{after.id}`",
                color=discord.Color.green(),
                timestamp=now_utc(),
            )
            await log_sanction(bot, after.guild, embed)

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
                timestamp=now_utc(),
            )
            await log_security(bot, member.guild, embed)
            return

        security = load_security()
        timestamps = security.get("join_timestamps", [])
        current_ts = now_utc().timestamp()
        timestamps = [ts for ts in timestamps if current_ts - ts <= RAID_TIME_WINDOW_SECONDS]
        timestamps.append(current_ts)
        security["join_timestamps"] = timestamps
        save_security(security)

        if len(timestamps) >= RAID_JOIN_THRESHOLD:
            await enable_raid_mode(
                bot,
                member.guild,
                f"{len(timestamps)} arrivées en moins de {RAID_TIME_WINDOW_SECONDS}s",
            )

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
                    timestamp=now_utc(),
                )
                await log_security(bot, member.guild, embed)
                return

        if ANTI_NO_AVATAR_ENABLED and not has_custom_avatar(member) and not is_whitelisted(member):
            embed = discord.Embed(
                title="⚠️ Compte sans avatar détecté",
                description=f"**Membre :** {member.mention}\n**ID :** `{member.id}`",
                color=discord.Color.orange(),
                timestamp=now_utc(),
            )
            await log_security(bot, member.guild, embed)
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
                timestamp=now_utc(),
            )
            await log_security(bot, member.guild, embed)
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
                    f"**Minimum attendu :** `3 jour(s)`"
                ),
                color=discord.Color.orange(),
                timestamp=now_utc(),
            )
            await log_verify_event(bot, member.guild, embed)
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
                timestamp=now_utc(),
            )
            await log_verify_event(bot, member.guild, embed)
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
            except Exception:
                pass

        vdata = load_json(VERIFY_FILE, {"users": {}, "panel_message_id": None})
        vdata["users"][str(member.id)] = {
            "captcha": random_code(),
            "verified": False,
            "tries": 0,
            "created_at": dt_to_iso(now_utc()),
            "updated_at": dt_to_iso(now_utc()),
        }
        save_json(VERIFY_FILE, vdata)

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
            timestamp=now_utc(),
        )
        await log_member_event(bot, member.guild, join_embed)
        await update_server_stats_once(bot)

    @bot.event
    async def on_member_remove(member: discord.Member):
        if member.guild.id != GUILD_ID:
            return

        leave_embed = discord.Embed(
            title="📤 Membre parti",
            description=(
                f"`{member}` a quitté le serveur.\n\n"
                f"**ID :** `{member.id}`\n"
                f"**Membres restants :** `{member.guild.member_count}`"
            ),
            color=discord.Color.red(),
            timestamp=now_utc(),
        )
        await log_member_event(bot, member.guild, leave_embed)
        await update_server_stats_once(bot)

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
                    timestamp=now_utc(),
                )
                await log_security(bot, message.guild, embed)

                if AUTO_TIMEOUT_ON_MASS_MENTION:
                    await timeout_member(
                        message.author,
                        AUTO_TIMEOUT_DURATION_MINUTES,
                        f"Mass mention ({mention_count})",
                    )
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
                    timestamp=now_utc(),
                )
                await log_security(bot, message.guild, embed)

                if AUTO_TIMEOUT_ON_LINK_SPAM:
                    await timeout_member(
                        message.author,
                        AUTO_TIMEOUT_DURATION_MINUTES,
                        f"Spam de liens ({len(tracker)} en {ANTI_LINK_SPAM_WINDOW}s)",
                    )

                tracker.clear()
                return

        await bot.process_commands(message)

    @tasks.loop(seconds=15)
    async def giveaway_watcher():
        from config import GIVEAWAYS_FILE
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
                await finish_giveaway(bot, message_id)

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

                vdata["users"].pop(user_id, None)
                changed = True

        if changed:
            save_json(VERIFY_FILE, vdata)

    @tasks.loop(minutes=1)
    async def update_server_stats_loop():
        await update_server_stats_once(bot)

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
            await disable_raid_mode(bot, guild)

    # expose helpers for commands
    bot.enable_raid_mode_helper = enable_raid_mode
    bot.disable_raid_mode_helper = disable_raid_mode
