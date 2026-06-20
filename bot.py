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
PREFIX = "?"
DEFAULT_EXAM_DATE = datetime.date(2026, 11, 29)  # Default fallback date

CONFIG_FILE = "config.json"
FONT_PATH = "assets/font.ttf"
BANNER_PATH = "assets/banner_base.png"

# ── Bot setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)
bot.remove_command('help')

# ── Persistent config helpers ────────────────────────────────────────────────

def load_config():
    """Load per-guild settings from disk."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(config):
    """Persist per-guild settings to disk."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)


config_data = load_config()

def get_exam_date(guild_id: str) -> datetime.date:
    """Get the specific exam date for a server, or fallback to default."""
    date_str = config_data.get(guild_id, {}).get("exam_date")
    if date_str:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    return DEFAULT_EXAM_DATE

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
        # Skip disabled servers
        if settings.get("disabled", False):
            continue

        exam_date = get_exam_date(guild_id)
        days_left = (exam_date - now.date()).days
        
        if days_left < 0:
            continue

        channel_ids = settings.get("channel_ids", [])
        post_time = settings.get("post_time")

        if not channel_ids or not post_time or current_time != post_time:
            continue

        for channel_id in channel_ids:
            channel = bot.get_channel(channel_id)
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
                print(f"Posted countdown to #{channel.name} ({guild_id})")
            except Exception as exc:
                print(f"Failed to post to #{channel.name}: {exc}")


@daily_countdown.before_loop
async def _wait_until_ready():
    await bot.wait_until_ready()

# ── Events & commands ────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    daily_countdown.start()


@bot.command(name="help")
async def custom_help(ctx):
    """List all available commands."""
    embed = discord.Embed(
        title="⏱️ CAT Countdown Bot Commands",
        description="Here are all the commands you can use to configure and use the bot.",
        color=0x10b981  # Terminal green to match the theme
    )
    
    embed.add_field(name="`?status`", value="Show the current countdown immediately.", inline=False)
    embed.add_field(name="`?next`", value="Show how much time is left until the next daily announcement.", inline=False)
    embed.add_field(name="`?countit`", value="Manually trigger the full daily post.\n*(Admin Only)*", inline=False)
    embed.add_field(name="`?addchannel #channel`", value="Add a channel for the daily countdown.\n*(Admin Only)*", inline=False)
    embed.add_field(name="`?remchannel`", value="Remove a channel from the daily countdown.\n*(Admin Only)*", inline=False)
    embed.add_field(name="`?listchannels`", value="List all channels anchored for the daily countdown.", inline=False)
    embed.add_field(name="`?settime HH:MM`", value="Set the time for the daily post (24-hour server time).\n*(Admin Only)*", inline=False)
    embed.add_field(name="`?setexamdate YYYY-MM-DD`", value="Set the target exam date for your server.\n*(Admin Only)*", inline=False)
    embed.add_field(name="`?disable`", value="Toggle the daily countdown on/off for this server.\n*(Admin Only)*", inline=False)
    
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def addchannel(ctx, channel: discord.TextChannel):
    """Add a channel for the daily countdown."""
    guild_id = str(ctx.guild.id)
    config_data.setdefault(guild_id, {}).setdefault("channel_ids", [])

    if channel.id in config_data[guild_id]["channel_ids"]:
        await ctx.send(f"⚠️ {channel.mention} is already in the countdown list.")
        return

    config_data[guild_id]["channel_ids"].append(channel.id)
    save_config(config_data)
    await ctx.send(f"✅ Added {channel.mention} to the daily countdown.")


@bot.command()
@commands.has_permissions(administrator=True)
async def remchannel(ctx):
    """Interactively remove a channel from the daily countdown."""
    guild_id = str(ctx.guild.id)
    channel_ids = config_data.get(guild_id, {}).get("channel_ids", [])

    if not channel_ids:
        await ctx.send("❌ No channels are set for the daily countdown.")
        return

    # Build a numbered list of channels
    lines = []
    for i, cid in enumerate(channel_ids, start=1):
        ch = bot.get_channel(cid)
        name = ch.mention if ch else f"Unknown (`{cid}`)"
        lines.append(f"**{i}.** {name}")

    listing = "\n".join(lines)
    await ctx.send(
        f"**Which channel do you want to remove?**\n{listing}\n\n"
        "Reply with the number (e.g. `1`)."
    )

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit()

    try:
        reply = await bot.wait_for("message", check=check, timeout=30)
    except Exception:
        await ctx.send("⏰ Timed out. No channel was removed.")
        return

    idx = int(reply.content) - 1
    if idx < 0 or idx >= len(channel_ids):
        await ctx.send("❌ Invalid number. No channel was removed.")
        return

    removed_id = channel_ids.pop(idx)
    save_config(config_data)

    ch = bot.get_channel(removed_id)
    name = ch.mention if ch else f"`{removed_id}`"
    await ctx.send(f"✅ Removed {name} from the daily countdown.")


@bot.command()
async def listchannels(ctx):
    """List all channels anchored for the daily countdown."""
    guild_id = str(ctx.guild.id)
    channel_ids = config_data.get(guild_id, {}).get("channel_ids", [])

    if not channel_ids:
        await ctx.send("📭 No channels are set for the daily countdown.")
        return

    lines = []
    for i, cid in enumerate(channel_ids, start=1):
        ch = bot.get_channel(cid)
        name = ch.mention if ch else f"Unknown (`{cid}`)"
        lines.append(f"**{i}.** {name}")

    embed = discord.Embed(
        title="📋 Countdown Channels",
        description="\n".join(lines),
        color=0x10b981,
    )
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def disable(ctx):
    """Toggle the daily countdown on or off for this server."""
    guild_id = str(ctx.guild.id)
    config_data.setdefault(guild_id, {})

    currently_disabled = config_data[guild_id].get("disabled", False)
    config_data[guild_id]["disabled"] = not currently_disabled
    save_config(config_data)

    if not currently_disabled:
        await ctx.send("⏸️ Daily countdown has been **disabled** for this server.")
    else:
        await ctx.send("▶️ Daily countdown has been **re-enabled** for this server.")


@bot.command()
@commands.has_permissions(administrator=True)
async def settime(ctx, time_str: str):
    """Set the daily post time (HH:MM, 24-hour format)."""
    try:
        parsed_time = datetime.datetime.strptime(time_str, "%H:%M")
        # Standardize the time string to always have a leading zero (e.g., "8:00" -> "08:00")
        time_str = parsed_time.strftime("%H:%M")
    except ValueError:
        await ctx.send("❌ Invalid format. Use `HH:MM` (24h), e.g. `08:00` or `14:30`.")
        return

    guild_id = str(ctx.guild.id)
    config_data.setdefault(guild_id, {})["post_time"] = time_str
    save_config(config_data)
    await ctx.send(f"✅ Daily countdown time set to `{time_str}` (server time)")


@bot.command()
@commands.has_permissions(administrator=True)
async def setexamdate(ctx, date_str: str):
    """Set the exam date (YYYY-MM-DD)."""
    try:
        datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        await ctx.send("❌ Invalid format. Use `YYYY-MM-DD`, e.g. `2026-11-29`.")
        return

    guild_id = str(ctx.guild.id)
    config_data.setdefault(guild_id, {})["exam_date"] = date_str
    save_config(config_data)
    await ctx.send(f"✅ Exam date set to `{date_str}`")


@bot.command(name="next")
async def next_cmd(ctx):
    """Show the time remaining until the next daily announcement."""
    guild_id = str(ctx.guild.id)
    post_time_str = config_data.get(guild_id, {}).get("post_time")
    
    if not post_time_str:
        await ctx.send("❌ No daily post time has been set for this server yet. An Admin must set it using `?settime HH:MM`.")
        return
        
    now = datetime.datetime.now()
    target_time = datetime.datetime.strptime(post_time_str, "%H:%M").time()
    next_post = datetime.datetime.combine(now.date(), target_time)
    
    # If the target time has already passed today, the next post is tomorrow
    if next_post < now:
        next_post += datetime.timedelta(days=1)
        
    delta = next_post - now
    total_seconds = int(delta.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    
    await ctx.send(f"⏳ The next announcement will be posted in **{hours} hours and {minutes} minutes**.")


@bot.command()
async def status(ctx):
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
        await ctx.send("⚠️ Base banner image not found. Place `banner_base.png` in the `assets/` directory.")

@bot.command()
@commands.has_permissions(administrator=True)
async def countit(ctx):
    """Post the full daily countdown message on demand."""
    guild_id = str(ctx.guild.id)
    exam_date = get_exam_date(guild_id)
    days_left = (exam_date - datetime.date.today()).days

    if days_left < 0:
        await ctx.send("The exam has already passed!")
        return

    try:
        img = generate_countdown_image(days_left)
        file = discord.File(fp=img, filename="countdown.png")
        msg = (
            f"**{days_left} Days until CAT 2026!**\n"
            "Keep grinding, stay focused, and make today count. Let's get it! 🚀"
        )
        await ctx.send(content=msg, file=file)
    except FileNotFoundError:
        await ctx.send("⚠️ Base banner image not found. Place `banner_base.png` in the `assets/` directory.")

# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: BOT_TOKEN is missing from .env")
    else:
        bot.run(TOKEN)
