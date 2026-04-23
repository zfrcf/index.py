import io
import random
import re
import string
from datetime import datetime, timedelta, timezone

import discord
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from config import (
    STAFF_ROLE_IDS,
    PARTNER_ROLE_IDS,
    GIVEAWAY_ALLOWED_ROLE_ID,
    TICKET_STAFF_ROLE_ID,
    VERIFIED_ROLE_ID,
    MIN_ACCOUNT_AGE_DAYS,
    ANTI_SUSPICIOUS_NAME_ENABLED,
    SUSPICIOUS_NAME_PATTERNS,
    ENABLE_ANTI_DOUBLE_HEURISTIC,
    ANTI_DOUBLE_LOOKBACK_DAYS,
)


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


def has_role(member: discord.Member, role_id: int) -> bool:
    return role_id != 0 and any(role.id == role_id for role in member.roles)


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
        color = (
            random.randint(180, 230),
            random.randint(180, 230),
            random.randint(180, 230),
        )
        draw.ellipse((x1, y1, x2, y2), outline=color, width=1)

    for _ in range(10):
        draw.line(
            (
                random.randint(0, width),
                random.randint(0, height),
                random.randint(0, width),
                random.randint(0, height),
            ),
            fill=(
                random.randint(70, 180),
                random.randint(70, 180),
                random.randint(70, 180),
            ),
            width=random.randint(1, 3),
        )

    font = get_font(42)
    x = 25
    for char in code:
        y = random.randint(20, 45)
        color = (
            random.randint(20, 90),
            random.randint(20, 90),
            random.randint(20, 90),
        )
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
            fill=(
                random.randint(120, 220),
                random.randint(120, 220),
                random.randint(120, 220),
            ),
        )

    image = image.filter(ImageFilter.SMOOTH)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def captcha_discord_file(code: str) -> discord.File:
    return discord.File(generate_captcha_image(code), filename="captcha.png")
