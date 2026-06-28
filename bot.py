import datetime
import io
import json
import os

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

# ── Configuration ────────────────────────────────────────────────────────────

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
PREFIX = ">"
DEFAULT_EXAM_DATE = datetime.date(2026, 11, 29)  # Default fallback date

CONFIG_FILE = "config.json"
FONT_PATH = "assets/font.ttf"
BANNER_PATH = "assets/banner_base.png"

# ── Bot setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)
bot.remove_command("help")

# ── Persistent config helpers ────────────────────────────────────────────────
#
# Config structure (per guild):
# {
#     "<guild_id>": {
#         "exam_date": "YYYY-MM-DD",          (optional, defaults to DEFAULT_EXAM_DATE)
#         "channels": {
#             "<channel_id>": ["HH:MM", ...]  (list of post times for this channel)
#         }
#     }
# }


def load_config():
    """Load per-guild settings from disk."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(cfg):
    """Persist per-guild settings to disk."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)


config_data = load_config()


def guild_cfg(guild_id: str) -> dict:
    """Return the config dict for a guild, creating it if needed."""
    return config_data.setdefault(guild_id, {})


def get_exam_date(guild_id: str) -> datetime.date:
    """Get the exam date for a server, or fallback to default."""
    date_str = config_data.get(guild_id, {}).get("exam_date")
    if date_str:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    return DEFAULT_EXAM_DATE


def get_channels(guild_id: str) -> dict:
    """Return the channels dict for a guild: {channel_id_str: [times]}."""
    return guild_cfg(guild_id).setdefault("channels", {})


def get_disabled(guild_id: str) -> list:
    """Return the list of disabled channel ID strings for a guild."""
    return guild_cfg(guild_id).setdefault("disabled_channels", [])

# ── Image generation ─────────────────────────────────────────────────────────

COUNTDOWN_FONT_SIZE = 250
TEXT_SHADOW_OFFSET = 8
TEXT_Y_NUDGE = -20
SHADOW_COLOR = (0, 0, 0, 150)
TEXT_COLOR = (16, 185, 129, 255)  # Terminal green


def generate_countdown_image(days_left: int) -> io.BytesIO:
    """Render *days_left* onto the base banner and return PNG bytes."""
    if not os.path.exists(BANNER_PATH):
        raise FileNotFoundError(
            f"Base banner '{BANNER_PATH}' not found. "
            "Place a banner_base.png in the assets/ directory."
        )

    img = Image.open(BANNER_PATH)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(FONT_PATH, COUNTDOWN_FONT_SIZE)
    except IOError:
        print("WARNING: Custom font not found, falling back to default.")
        font = ImageFont.load_default()

    text = str(days_left)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (img.width - text_w) / 2
    y = (img.height - text_h) / 2 + TEXT_Y_NUDGE

    draw.text((x + TEXT_SHADOW_OFFSET, y + TEXT_SHADOW_OFFSET), text, font=font, fill=SHADOW_COLOR)
    draw.text((x, y), text, font=font, fill=TEXT_COLOR)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ── Daily countdown task ─────────────────────────────────────────────────────

@tasks.loop(minutes=1)
async def daily_countdown():
    """Check once per minute whether it's time to post the countdown."""
    now = datetime.datetime.now()
    current_time = now.strftime("%H:%M")

    for guild_id, settings in config_data.items():
        exam_date = get_exam_date(guild_id)
        days_left = (exam_date - now.date()).days

        if days_left < 0:
            continue

        channels = settings.get("channels", {})

        disabled = settings.get("disabled_channels", [])

        for channel_id_str, times in channels.items():
            if channel_id_str in disabled:
                continue
            if current_time not in times:
                continue

            channel = bot.get_channel(int(channel_id_str))
            if channel is None:
                continue

            try:
                img = generate_countdown_image(days_left)
                file = discord.File(fp=img, filename="countdown.png")
                await channel.send(
                    content=(
                        f"**{days_left} Days until CAT 2026!**\n"
                        "Keep grinding, stay focused, and make today count. Let's get it! 🚀"
                    ),
                    file=file,
                )
                print(f"Posted countdown to #{channel.name} ({guild_id}) at {current_time}")
            except Exception as exc:
                print(f"Failed to post to channel {channel_id_str}: {exc}")


@daily_countdown.before_loop
async def _wait_until_ready():
    await bot.wait_until_ready()

# ── Events & commands ────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    daily_countdown.start()


# ── >help ─────────────────────────────────────────────────────────────────────

@bot.command(name="help")
async def cmd_help(ctx):
    """List all available commands."""
    embed = discord.Embed(
        title="⏱️ CAT Countdown Bot",
        description="All commands use the `>` prefix.",
        color=0x10b981,
    )
    embed.add_field(name="`>status`", value="Show the current countdown.", inline=False)
    embed.add_field(name="`>next`", value="Time until the next post in **this** channel.", inline=False)
    embed.add_field(name="`>set #channel HH:MM`", value="Add a post time for a channel.\n*(Admin)*", inline=False)
    embed.add_field(name="`>rem`", value="Remove a channel or a specific time.\n*(Admin)*", inline=False)
    embed.add_field(name="`>list`", value="List all channels and their scheduled times.", inline=False)
    embed.add_field(name="`>toggle #channel`", value="Toggle a channel's daily post on/off.\n*(Admin)*", inline=False)
    embed.add_field(name="`>exam YYYY-MM-DD`", value="Set the target exam date.\n*(Admin)*", inline=False)
    await ctx.send(embed=embed)


# ── >status ───────────────────────────────────────────────────────────────────

@bot.command(name="status")
async def cmd_status(ctx):
    """Show the current countdown immediately."""
    guild_id = str(ctx.guild.id)
    exam_date = get_exam_date(guild_id)
    days_left = (exam_date - datetime.date.today()).days

    if days_left < 0:
        await ctx.send("The exam has already passed!")
        return

    try:
        img = generate_countdown_image(days_left)
        file = discord.File(fp=img, filename="status.png")
        await ctx.send(f"**{days_left} days left** until CAT 2026!", file=file)
    except FileNotFoundError:
        await ctx.send("⚠️ Banner image not found. Place `banner_base.png` in `assets/`.")


# ── >next ─────────────────────────────────────────────────────────────────────

@bot.command(name="next")
async def cmd_next(ctx):
    """Show time remaining until the next post in THIS channel."""
    guild_id = str(ctx.guild.id)
    channel_id = str(ctx.channel.id)
    channels = get_channels(guild_id)
    times = channels.get(channel_id, [])

    if not times:
        await ctx.send("❌ No post times are set for this channel. Use `>set` to add one.")
        return

    now = datetime.datetime.now()
    today = now.date()
    deltas = []

    for t_str in times:
        target_time = datetime.datetime.strptime(t_str, "%H:%M").time()
        target_dt = datetime.datetime.combine(today, target_time)
        if target_dt <= now:
            target_dt += datetime.timedelta(days=1)
        deltas.append((target_dt - now, t_str))

    deltas.sort()
    nearest_delta, nearest_time = deltas[0]

    total_seconds = int(nearest_delta.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    await ctx.send(
        f"⏳ Next post in this channel at **{nearest_time}** — "
        f"**{hours}h {minutes}m** from now."
    )


# ── >set ──────────────────────────────────────────────────────────────────────

@bot.command(name="set")
@commands.has_permissions(administrator=True)
async def cmd_set(ctx, channel: discord.TextChannel, time_str: str):
    """Add a post time for a specific channel.  Usage: >set #channel HH:MM"""
    # Validate time format
    try:
        parsed = datetime.datetime.strptime(time_str, "%H:%M")
        time_str = parsed.strftime("%H:%M")  # normalise e.g. "8:00" → "08:00"
    except ValueError:
        await ctx.send("❌ Invalid time. Use `HH:MM` (24h), e.g. `08:00` or `14:30`.")
        return

    guild_id = str(ctx.guild.id)
    channels = get_channels(guild_id)
    channel_id = str(channel.id)
    times = channels.setdefault(channel_id, [])

    if time_str in times:
        await ctx.send(
            f"⚠️ {channel.mention} already has `{time_str}` scheduled. "
            f"Use `>rem` to remove it first."
        )
        return

    times.append(time_str)
    times.sort()
    save_config(config_data)
    await ctx.send(f"✅ {channel.mention} will now post daily at `{time_str}`.")


# ── >list ─────────────────────────────────────────────────────────────────────

@bot.command(name="list")
async def cmd_list(ctx):
    """List all channels and their scheduled post times."""
    guild_id = str(ctx.guild.id)
    channels = get_channels(guild_id)

    if not channels:
        await ctx.send("📭 No channels are configured. Use `>set #channel HH:MM` to add one.")
        return

    disabled = get_disabled(guild_id)

    lines = []
    for cid, times in channels.items():
        ch = bot.get_channel(int(cid))
        name = ch.mention if ch else f"Unknown (`{cid}`)"
        times_fmt = ", ".join(f"`{t}`" for t in sorted(times)) if times else "_no times set_"
        tag = "  ⏸️" if cid in disabled else ""
        lines.append(f"• {name} — {times_fmt}{tag}")

    embed = discord.Embed(
        title="📋 Scheduled Channels",
        description="\n".join(lines),
        color=0x10b981,
    )

    # Show exam date in footer
    exam_date = get_exam_date(guild_id)
    embed.set_footer(text=f"Exam date: {exam_date.strftime('%Y-%m-%d')}")

    await ctx.send(embed=embed)


# ── >rem ──────────────────────────────────────────────────────────────────────

@bot.command(name="rem")
@commands.has_permissions(administrator=True)
async def cmd_rem(ctx):
    """Interactively remove a channel or a specific time from a channel."""
    guild_id = str(ctx.guild.id)
    channels = get_channels(guild_id)

    if not channels:
        await ctx.send("❌ No channels are configured yet.")
        return

    # Build a flat numbered list:  each entry is (channel_id, time_or_None)
    # If a channel has one time  → one entry to remove the whole channel+time
    # If a channel has many times → one entry per time, plus one to remove entire channel
    entries = []  # list of (channel_id_str, time_str_or_None, display_text)

    for cid, times in channels.items():
        ch = bot.get_channel(int(cid))
        name = ch.mention if ch else f"Unknown (`{cid}`)"

        if len(times) <= 1:
            # Single time or no times — offer to remove the whole channel
            t = times[0] if times else "no time"
            entries.append((cid, None, f"{name} — `{t}` *(remove channel)*"))
        else:
            # Multiple times — offer per-time removal AND whole-channel removal
            for t in sorted(times):
                entries.append((cid, t, f"{name} — remove `{t}`"))
            entries.append((cid, None, f"{name} — **remove entire channel**"))

    lines = [f"**{i+1}.** {e[2]}" for i, e in enumerate(entries)]
    await ctx.send(
        "**What do you want to remove?**\n"
        + "\n".join(lines)
        + "\n\nReply with a number."
    )

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit()

    try:
        reply = await bot.wait_for("message", check=check, timeout=30)
    except Exception:
        await ctx.send("⏰ Timed out. Nothing was removed.")
        return

    idx = int(reply.content) - 1
    if idx < 0 or idx >= len(entries):
        await ctx.send("❌ Invalid number.")
        return

    target_cid, target_time, _ = entries[idx]
    ch = bot.get_channel(int(target_cid))
    name = ch.mention if ch else f"`{target_cid}`"

    if target_time is None:
        # Remove the entire channel
        del channels[target_cid]
        save_config(config_data)
        await ctx.send(f"✅ Removed {name} from the schedule entirely.")
    else:
        # Remove just that one time
        channels[target_cid].remove(target_time)
        # If no times left, remove the channel entry too
        if not channels[target_cid]:
            del channels[target_cid]
        save_config(config_data)
        await ctx.send(f"✅ Removed `{target_time}` from {name}.")


# ── >toggle ───────────────────────────────────────────────────────────────────

@bot.command(name="toggle")
@commands.has_permissions(administrator=True)
async def cmd_toggle(ctx, channel: discord.TextChannel):
    """Toggle a channel's daily post on or off.  Usage: >toggle #channel"""
    guild_id = str(ctx.guild.id)
    channel_id = str(channel.id)
    channels = get_channels(guild_id)

    if channel_id not in channels:
        await ctx.send(f"❌ {channel.mention} isn't in the schedule. Use `>set` first.")
        return

    disabled = get_disabled(guild_id)

    if channel_id in disabled:
        disabled.remove(channel_id)
        save_config(config_data)
        await ctx.send(f"▶️ {channel.mention} has been **re-enabled**.")
    else:
        disabled.append(channel_id)
        save_config(config_data)
        await ctx.send(f"⏸️ {channel.mention} has been **disabled**.")


# ── >exam ─────────────────────────────────────────────────────────────────────

@bot.command(name="exam")
@commands.has_permissions(administrator=True)
async def cmd_exam(ctx, date_str: str):
    """Set the target exam date.  Usage: >exam YYYY-MM-DD"""
    try:
        datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        await ctx.send("❌ Invalid format. Use `YYYY-MM-DD`, e.g. `2026-11-29`.")
        return

    guild_id = str(ctx.guild.id)
    guild_cfg(guild_id)["exam_date"] = date_str
    save_config(config_data)
    await ctx.send(f"✅ Exam date set to `{date_str}`.")

# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: BOT_TOKEN is missing from .env")
    else:
        bot.run(TOKEN)
