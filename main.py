# main.py — полный файл (обновлённый: расширенная фильтрация ссылок + CESU DM при принятии)
import discord
from discord.ext import commands
import sqlite3
import os
import io
from PIL import Image
from moviepy.editor import VideoFileClip, ImageSequenceClip
import uuid
import re
import asyncio
from discord import Embed, Color
from datetime import datetime, timedelta, timezone
from discord.ui import View, button, Modal, TextInput
import aiohttp
import requests
from collections import defaultdict, deque
import time
from urllib.parse import urlparse, unquote
import idna
import openai

# --- Ключи (не меняем имя VIRUSTOTAL_KEY) ---
VIRUSTOTAL_KEY = os.getenv("VIRUSTOTAL_KEY")
GOOGLE_SAFEBROWSING_KEY = os.getenv("GOOGLE_SAFEBROWSING_KEY")
# Подключение API ключа OpenAI из переменной окружения
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat"

# -----------------------
# Расширенные списки фильтрации (заменяют предыдущие SUSPICIOUS_KEYWORDS / WHITELIST_DOMAINS)
# -----------------------
# ключевые слова, которые мы считаем подозрительными при их наличии в домене
SUSPICIOUS_KEYWORDS = [
    # NSFW / adult
    "porn", "porno", "xxx", "sex", "adult", "erotic", "erotica", "fetish",
    "fetlife", "cam", "cams", "tube", "sexchat", "onlyfans", "adultfriendfinder",
    "escort", "escortservice", "webcam", "nsfw", "honeypot",
    # gambling
    "casino", "casinos", "bet", "bets", "betting", "poker", "slot", "slots",
    "roulette", "blackjack", "baccarat", "spin", "jackpot", "1xbet", "bet365",
    "stake", "pinnacle", "bookmaker", "betway", "pokerstars",
    # scams / freebies / nitro / robux
    "free-robux", "robuxfree", "freegift", "free-nitro", "nitro-free", "getnitro",
    "nitrogiveaway", "nitroclaim", "discord-nitro", "discordgift", "discord-gift",
    "hack", "cheat", "generator", "gens", "prize", "claim", "giveaway", "earn",
    # phishing / account theft
    "login", "signin", "securelogin", "account-recovery", "accountverify",
    "verify-account", "verify", "verification", "auth", "password-reset", "login-roblox",
    "paypal-secure", "wallet-connect", "metamasklogin"
]

# конкретные домены / сильные индикаторы (поддомены тоже блокируются)
SUSPICIOUS_DOMAINS = {
    "pornhub.com", "xvideos.com", "xnxx.com", "xhamster.com", "redtube.com",
    "youporn.com", "tube8.com", "tnaflix.com", "spankwire.com", "brazzers.com",
    "onlyfans.com", "cams.com", "adultfriendfinder.com", "cam4.com",
    "1xbet.com", "bet365.com", "betfair.com", "stake.com", "pokerstars.com",
    "betway.com", "bovada.lv", "draftkings.com", "fanduel.com", "casino.com",
    "free-robux.com", "robux-free.com", "nitro.gifts", "verify-nitro.com",
    "discord-nitro.com", "discordgift.com", "discord-gift.com", "nitroclaim.xyz",
    # добавляй сюда свои известные мошеннические домены
}

# подозрительные фрагменты в пути/параметрах запроса
SUSPICIOUS_PATH_KEYWORDS = [
    "claim", "free", "giveaway", "get-nitro", "nitro", "free-robux",
    "verify", "verification", "password", "reset", "voucher", "coupon", "earn"
]

# whitelist — если домен содержит любой из этих фрагментов — не фильтруем
WHITELIST_DOMAINS = {"discord.com", "discord.gg", "youtube.com", "roblox.com", "google.com", "github.com"}

# регулярка для поиска ссылок (оставляем как у тебя)
URL_REGEX = re.compile(r"(https?://[^\s]+)")

# кеш VirusTotal: url -> (bool_is_bad, timestamp)
_vt_cache = {}
_VT_CACHE_TTL = 60 * 60  # 1 час



DISCORD_TOKEN_ENV_CANDIDATES = ("DISCORD_TOKEN", "BOT_TOKEN", "TOKEN")


def sanitize_discord_token(raw: str | None) -> str:
    """Убирает частые артефакты из токена (пробелы, кавычки, префикс Bot)."""
    token = (raw or "").strip().strip('"').strip("'")
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    return token


def resolve_discord_token():
    """Ищет токен в стандартных env-именах и возвращает (token, source_env)."""
    for env_name in DISCORD_TOKEN_ENV_CANDIDATES:
        value = os.getenv(env_name)
        if value:
            return sanitize_discord_token(value), env_name
    return "", None


def looks_like_discord_token(token: str) -> bool:
    """Быстрая валидация формата токена до попытки логина."""
    parts = token.split(".")
    return len(parts) == 3 and all(parts)
def collect_runtime_health() -> dict:
    """Собирает ключевые параметры окружения для быстрой диагностики."""
    token, token_source = resolve_discord_token()
    return {
        "DISCORD_TOKEN": bool(token),
        "DISCORD_TOKEN_SOURCE": token_source or "not-set",
        "VIRUSTOTAL_KEY": bool(VIRUSTOTAL_KEY),
        "GOOGLE_SAFEBROWSING_KEY": bool(GOOGLE_SAFEBROWSING_KEY),
        "DEEPSEEK_API_KEY": bool(DEEPSEEK_API_KEY),
    }

def _normalize_domain(raw_domain: str) -> str:
    """
    Приводим домен к безопасной форме: убираем порт, lowercase, punycode если нужно.
    """
    try:
        d = raw_domain.split(":")[0].lower()
        try:
            d = idna.encode(d).decode("ascii")
        except Exception:
            pass
        return d
    except Exception:
        return raw_domain.lower()

def _domain_matches_blacklist(domain: str) -> bool:
    dom = _normalize_domain(domain)
    # прямой домен / поддомен
    for bad in SUSPICIOUS_DOMAINS:
        if dom == bad or dom.endswith("." + bad):
            return True
    # ключевые слова в домене
    for kw in SUSPICIOUS_KEYWORDS:
        if kw in dom:
            return True
    return False

# -----------------------
# Google Safe Browsing проверка
# -----------------------
async def check_google_safe_browsing(session: aiohttp.ClientSession, url: str) -> bool:
    """
    Возвращает True если Google Safe Browsing пометил URL как опасный.
    Требует GOOGLE_SAFEBROWSING_KEY в окружении.
    """
    key = GOOGLE_SAFEBROWSING_KEY
    if not key:
        return False
    endpoint = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={key}"
    payload = {
        "client": {"clientId": "discord-bot", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url}]
        }
    }
    try:
        async with session.post(endpoint, json=payload, timeout=10) as resp:
            if resp.status != 200:
                return False
            js = await resp.json()
            return "matches" in js and bool(js["matches"])
    except Exception as e:
        print(f"GSB error for {url}: {e}")
        return False

# -----------------------
# VirusTotal проверка (с использованием VIRUSTOTAL_KEY)
# -----------------------
async def check_virustotal(session: aiohttp.ClientSession, url: str) -> bool:
    """
    Проверка URL через VirusTotal API. True = вредоносный/suspicious.
    Использует переменную окружения VIRUSTOTAL_KEY (не меняю название).
    """
    if not VIRUSTOTAL_KEY:
        return False

    headers = {"x-apikey": VIRUSTOTAL_KEY}
    try:
        # 1) Отправляем URL на анализ
        async with session.post(
            "https://www.virustotal.com/api/v3/urls",
            data={"url": url},
            headers=headers,
            timeout=15
        ) as resp:
            if resp.status not in (200, 201):
                return False
            js = await resp.json()
            url_id = js.get("data", {}).get("id")
            if not url_id:
                return False

        # 2) Получаем результат анализа
        async with session.get(
            f"https://www.virustotal.com/api/v3/analyses/{url_id}",
            headers=headers,
            timeout=15
        ) as resp2:
            if resp2.status != 200:
                return False
            res = await resp2.json()
            stats = res.get("data", {}).get("attributes", {}).get("stats", {})
            malicious = stats.get("malicious", 0)
            suspicious = stats.get("suspicious", 0)

            return (malicious + suspicious) > 0

    except Exception as e:
        print(f"Ошибка VirusTotal: {e}")
        return False

# -----------------------
# Основная проверка ссылок — интегрируем локальный фильтр + GSB + VT + кеш
# -----------------------
async def check_and_handle_urls(message: discord.Message) -> bool:
    """Проверяет ссылки в сообщении. True = сообщение обработано (удалено/замут)."""
    text = message.content or ""
    urls = URL_REGEX.findall(text)
    if not urls:
        return False

    suspicious = []
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                parsed = urlparse(url if url.startswith("http") else "http://" + url)
                domain = parsed.netloc or ""
                domain = domain.lower()
                path = (parsed.path or "") + ("?" + (parsed.query or "") if parsed.query else "")
                try:
                    path_dec = unquote(path).lower()
                except Exception:
                    path_dec = path.lower()
            except Exception:
                continue

            # whitelist
            if any(wd in domain for wd in WHITELIST_DOMAINS):
                continue

            # 1) локальные домены/ключевики
            if _domain_matches_blacklist(domain):
                suspicious.append((url, "local-domain/keyword"))
                continue

            # 2) путь/параметры (claim-free-nitro и т.д.)
            for pk in SUSPICIOUS_PATH_KEYWORDS:
                if pk in path_dec:
                    suspicious.append((url, f"path={pk}"))
                    break
            if any(u == url for u, _ in suspicious):
                continue

            # 3) кеш VT
            try:
                cached = _vt_cache.get(url)
                if cached and (time.time() - cached[1]) < _VT_CACHE_TTL:
                    if cached[0]:
                        suspicious.append((url, "vt-cache"))
                        continue
                    else:
                        continue
            except Exception:
                pass

            # 4) Google Safe Browsing
            try:
                gsb_bad = await check_google_safe_browsing(session, url)
                if gsb_bad:
                    suspicious.append((url, "GoogleSafeBrowsing"))
                    _vt_cache[url] = (True, time.time())
                    continue
            except Exception as e:
                print(f"GSB check error: {e}")

            # 5) VirusTotal
            try:
                vt_bad = await check_virustotal(session, url)
                _vt_cache[url] = (bool(vt_bad), time.time())
                if vt_bad:
                    suspicious.append((url, "VirusTotal"))
                    continue
            except Exception as e:
                print(f"VirusTotal check error: {e}")

    if not suspicious:
        return False

    reason_text = "\n".join(f"{u} -> {why}" for u, why in suspicious)
    reasons = [f"Подозрительная ссылка: {u} ({why})" for u, why in suspicious]

    try:
        await message.delete()
    except Exception:
        pass

    await apply_max_timeout(message.author, "Автомодерация: опасные ссылки")
    await log_violation_with_evidence(message, "🚨 Опасная ссылка", reasons)

    try:
        dm = Embed(
            title="⚠️ Ссылка заблокирована",
            description="Ваше сообщение удалено: обнаружены подозрительные ссылки. Вам выдано ограничение голоса на 24 часа.",
            color=Color.red()
        )
        dm.add_field(name="Детали", value=f"```{reason_text[:1900]}```", inline=False)
        await message.author.send(embed=dm)
    except Exception:
        pass

    return True

# -----------------------
# ConfirmView для выдачи ролей (единый отряд TAS)
# -----------------------
class ConfirmView(View):
    def __init__(self, allowed_checker_role_ids, target_message, squad_name, role_ids, target_user_id):
        """
        allowed_checker_role_ids: список id ролей, которые имеют право нажимать кнопки подтверждения
        target_message: исходное сообщение (объект discord.Message) от пользователя, которое было отправлено в форму
        squad_name: строка с названием отряда
        role_ids: список id ролей, которые будут выданы после одобрения (rewards)
        target_user_id: id пользователя, которого нужно зачислить
        """
        super().__init__(timeout=None)
        self.allowed_checker_role_ids = allowed_checker_role_ids
        self.message = target_message
        self.squad_name = squad_name
        self.role_ids = role_ids
        self.target_user_id = target_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Проверяем есть ли у нажавшего одна из разрешённых ролей
        if any(r.id in self.allowed_checker_role_ids for r in interaction.user.roles):
            return True
        await interaction.response.send_message("❌ У тебя нет прав нажимать эту кнопку.", ephemeral=True)
        return False

    @button(label="Принять", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.guild.get_member(self.target_user_id)
        if member:
            for role_id in self.role_ids:
                role = interaction.guild.get_role(role_id)
                if role:
                    try:
                        await member.add_roles(role)
                    except Exception:
                        pass
            try:
                dm_embed = Embed(
                    title="⏳ Ваша заявка принята!",
                    description="Вы успешно прошли предварительное подтверждение. Ожидайте связи от офицера отряда.",
                    color=Color.green()
                )
                await member.send(embed=dm_embed)
            except Exception:
                pass
            # Ответ в канале (как было)
            try:
                await self.message.reply(embed=Embed(title="✅ Принято", description=f"Вы зачислены в отряд **{self.squad_name}**!", color=Color.green()))
            except Exception:
                pass
        await interaction.response.send_message("Принято.", ephemeral=True)
        self.stop()

    @button(label="Отказать", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.message.add_reaction("❌")
        except Exception:
            pass
        await interaction.response.send_message("Отказ зарегистрирован.", ephemeral=True)
        self.stop()

# -----------------------
# (Дальше остальной твой код — без изменений)
# -----------------------

# --- Приветствие и прощание ---
WELCOME_CHANNEL_ID = 1351880905936867328
GOODBYE_CHANNEL_ID = 1351880978808963073

# --- Константы / настройки ---
allowed_role_ids = [1340596390614532127, 1341204231419461695]
allowed_guild_ids = [1340594372596469872]
sbor_channels = {}

FORM_CHANNEL_ID = 1340996239113850971

# Единый отряд TAS (заменяет старые GOT/CESU формы)
TAS_CHANNEL_ID = 1394635110665556009
TAS_REVIEWER_ROLE_IDS = [1341041194733670401, 1341040607728107591, 1341040703551307846]
TAS_ROLE_REWARDS = [1341040784723411017, 1341040871562285066, 1341100562783014965, 1341039967555551333]

VOICE_TIMEOUT_HOURS = 24
VIOLATION_ATTACHMENT_LIMIT = 3
NSFW_FILENAME_KEYWORDS = ["porn", "sex", "xxx", "nsfw", "erotic", "nud", "18+"]
AD_FILENAME_KEYWORDS = ["casino", "bet", "free", "nitro", "robux", "giveaway", "promo", "advert"]
AD_TEXT_KEYWORDS = ["подпишись", "реклама", "промокод", "ставки", "казино", "розыгрыш", "nitro", "robux", "бонус"]
SUSPICIOUS_INVITE_PATTERNS = ["t.me/", "vk.com/"]
ALLOWED_DISCORD_INVITE_PATTERNS = ["discord.gg/", "discord.com/invite/"]

# PSC (embed с логотипом) канал и роль для пинга
PSC_CHANNEL_ID = 1416417030520967199
PING_ROLE_ID = 1341168051269275718

# STOPREID (анти-спам)
SPAM_WINDOW_SECONDS = 10     # окно времени в секундах для проверки повторов
SPAM_DUPLICATES_THRESHOLD = 4  # сколько одинаковых сообщений -> действие
SPAM_LOG_CHANNEL = 1347412933986226256
MUTE_ROLE_ID = 1426552449874919624

# Жалобы
COMPLAINT_INPUT_CHANNEL = 1404977876184334407
COMPLAINT_NOTIFY_ROLE = 1341203508606533763

# Лог канал (для наказаний и т.д.)
log_channel_id = 1392125177399218186

# Канал для форм наказаний (используется в логике наказаний)
form_channel_id = 1349725568371003392  # <- если нужно, поменяй на свой ID

# Наказания (роли)
punishment_roles = {
    "1 выговор": 1341379345322610698,
    "2 выговора": 1341379426314620992,
    "1 страйк": 1341379475681841163,
    "2 страйка": 1341379529997815828
}

squad_roles = {
    "got_base": 1341040784723411017,
    "got_notify": 1341041194733670401,
    "cesu_base": 1341100562783014965,
    "cesu_notify": 1341040607728107591
}

# --- Инициализация бота ---
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Отключаем встроенный voice client, если окружение не поддерживает audioop (render)
try:
    import audioop  # noqa
except Exception:
    discord.VoiceClient = None

# --- DB (остается, если нужно) ---
conn = sqlite3.connect("bot_data.db")
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS private_chats (id INTEGER PRIMARY KEY AUTOINCREMENT, user1_id INTEGER, user2_id INTEGER, password TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, sender_id INTEGER, message TEXT, file BLOB, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
conn.commit()

# -----------------------
# Утилитарные функции
# -----------------------
def extract_clean_keyword(text: str):
    return re.sub(r"[^a-z]", "", (text or "").lower())

async def safe_send_dm(user: discord.User, embed: Embed, file: discord.File = None):
    try:
        if file:
            await user.send(embed=embed, file=file)
        else:
            await user.send(embed=embed)
    except Exception:
        # Нельзя отправить ЛС — игнорируем
        pass


def assess_applicant_risk(roblox_nick: str, discord_nick: str, member: discord.Member):
    reasons = []
    rb = (roblox_nick or "").strip()
    dn = (discord_nick or "").strip()

    if re.search(r"https?://|discord\.gg|t\.me", rb + " " + dn, re.IGNORECASE):
        reasons.append("в нике есть ссылка/инвайт")
    if any(ch in rb + dn for ch in ["$", "@everyone", "@here"]):
        reasons.append("подозрительные символы в нике")
    if len(re.sub(r"\D", "", rb)) >= 5 or len(re.sub(r"\D", "", dn)) >= 5:
        reasons.append("много цифр в никнейме")
    if member and member.created_at:
        created = member.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created).days
        if age_days < 14:
            reasons.append(f"свежий Discord-аккаунт ({age_days} дн.)")
    return reasons


async def apply_max_timeout(member: discord.Member, reason: str):
    until = datetime.now(timezone.utc) + timedelta(hours=VOICE_TIMEOUT_HOURS)

    me = member.guild.me if member.guild else None
    if me and not me.guild_permissions.moderate_members:
        print("Не удалось выдать ограничение голоса: у бота нет права Moderate Members")
        return False

    try:
        await member.edit(timed_out_until=until, reason=reason)
        return True
    except TypeError:
        # fallback для старых сигнатур discord.py
        try:
            await member.edit(timeout=until, reason=reason)
            return True
        except Exception as e:
            print(f"Не удалось выдать ограничение голоса: {e}")
            return False
    except Exception as e:
        print(f"Не удалось выдать ограничение голоса: {e}")
        return False


async def log_violation_with_evidence(message: discord.Message, title: str, reasons: list[str]):
    if not message.guild:
        return
    log_chan = message.guild.get_channel(SPAM_LOG_CHANNEL)
    if not log_chan:
        return

    emb = Embed(title=title, color=Color.red(), timestamp=datetime.utcnow())
    emb.add_field(name="Пользователь", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
    emb.add_field(name="Канал", value=message.channel.mention, inline=False)
    emb.add_field(name="Причины", value="\n".join(f"• {r}" for r in reasons)[:1024], inline=False)
    emb.add_field(name="Контент", value=(message.content or "(без текста)")[:1000], inline=False)

    if message.attachments:
        links = "\n".join(a.url for a in message.attachments[:5])
        emb.add_field(name="Вложения (URL)", value=links[:1024], inline=False)

    files = []
    for a in message.attachments[:VIOLATION_ATTACHMENT_LIMIT]:
        try:
            files.append(await a.to_file())
        except Exception:
            pass

    try:
        if files:
            await log_chan.send(embed=emb, files=files)
        else:
            await log_chan.send(embed=emb)
    except Exception as e:
        print(f"Ошибка логирования нарушения: {e}")


def detect_advertising_or_scam_text(text: str):
    t = (text or "").lower()
    reasons = []

    # Обычные Discord-инвайты сами по себе не считаем рекламой.
    has_discord_invite = any(p in t for p in ALLOWED_DISCORD_INVITE_PATTERNS)

    if any(p in t for p in SUSPICIOUS_INVITE_PATTERNS):
        reasons.append("инвайт/внешняя ссылка для рекламы")

    # Ключевики рекламы/скама считаем нарушением.
    # Но если в сообщении только Discord-инвайт без рекламных маркеров — не триггерим.
    if any(k in t for k in AD_TEXT_KEYWORDS):
        reasons.append("рекламные/скам ключевые слова")

    if has_discord_invite and reasons == ["инвайт/внешняя ссылка для рекламы"]:
        return []

    return reasons


def detect_attachment_violations(attachments):
    reasons = []
    for att in attachments:
        name = (att.filename or "").lower()
        ctype = (att.content_type or "").lower()
        if any(k in name for k in NSFW_FILENAME_KEYWORDS):
            reasons.append(f"NSFW по имени файла: {att.filename}")
        if any(k in name for k in AD_FILENAME_KEYWORDS):
            reasons.append(f"реклама/скам по имени файла: {att.filename}")
        if ctype.startswith("video/") and any(k in name for k in ["casino", "porn", "nitro", "robux"]):
            reasons.append(f"подозрительное видео: {att.filename}")
    return reasons

# -----------------------
# КОМАНДЫ: gif + sbor (как у тебя были)
# -----------------------
@bot.command(name='gif')
async def gif(ctx):
    if not ctx.message.attachments:
        await ctx.send("Пожалуйста, прикрепи изображение или видео к команде.")
        return

    image_files = []
    video_files = []
    os.makedirs("temp", exist_ok=True)

    for attachment in ctx.message.attachments:
        filename = attachment.filename
        ext = os.path.splitext(filename)[1].lower().strip(".")
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        file_path = os.path.join("temp", unique_name)
        await attachment.save(file_path)

        if ext in ['jpg', 'jpeg', 'png', 'webp', 'bmp', 'heic']:
            image_files.append(file_path)
        elif ext in ['mp4', 'mov', 'webm', 'avi', 'mkv']:
            video_files.append(file_path)
        else:
            await ctx.send(f"❌ Файл `{filename}` не поддерживается.")
            os.remove(file_path)
            return

    output_path = f"temp/{uuid.uuid4().hex}.gif"
    try:
        if image_files:
            clip = ImageSequenceClip(image_files, fps=1)
            clip.write_gif(output_path, fps=1)
        elif video_files:
            clip = VideoFileClip(video_files[0])
            clip = clip.subclip(0, min(5, clip.duration))
            clip.write_gif(output_path)
        else:
            await ctx.send("❌ Не удалось обработать вложения.")
            return

        await ctx.send(file=discord.File(output_path))
    except Exception as e:
        await ctx.send(f"❌ Ошибка при создании GIF: {e}")
    finally:
        for f in image_files + video_files:
            if os.path.exists(f):
                os.remove(f)
        if os.path.exists(output_path):
            os.remove(output_path)


@bot.command(name="health")
async def health(ctx):
    """Показывает состояние бота и наличие ключей окружения."""
    runtime = collect_runtime_health()
    status_lines = [
        f"`{name}`: {'✅ OK' if enabled else '⚠️ отсутствует'}"
        for name, enabled in runtime.items()
    ]

    embed = Embed(
        title="Состояние бота",
        description="\n".join(status_lines),
        color=Color.green() if runtime["DISCORD_TOKEN"] else Color.orange(),
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="Latency", value=f"`{round(bot.latency * 1000)}ms`", inline=False)
    await ctx.send(embed=embed)

@bot.tree.command(name="sbor", description="Начать сбор: создаёт голосовой канал и пингует роль")
@discord.app_commands.describe(role="Роль, которую нужно пинговать")
async def sbor(interaction: discord.Interaction, role: discord.Role):
    if interaction.guild.id not in allowed_guild_ids:
        await interaction.response.send_message("❌ Команда недоступна на этом сервере.", ephemeral=True)
        return

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not any(r.id in allowed_role_ids for r in member.roles):
        await interaction.response.send_message("❌ У тебя нет прав для этой команды.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    existing = discord.utils.get(interaction.guild.voice_channels, name="сбор")
    if existing:
        await interaction.followup.send("❗ Канал 'сбор' уже существует.")
        return

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(connect=False),
        role: discord.PermissionOverwrite(connect=True, view_channel=True)
    }

    category = interaction.channel.category
    voice_channel = await interaction.guild.create_voice_channel("Сбор", overwrites=overwrites, category=category)
    sbor_channels[interaction.guild.id] = voice_channel.id

    webhook = await interaction.channel.create_webhook(name="Сбор")
    await webhook.send(
        content=f"**Сбор! {role.mention}. Заходите в <#{voice_channel.id}>!**",
        username="Сбор",
        avatar_url=bot.user.avatar.url if bot.user.avatar else None
    )
    await webhook.delete()
    await interaction.followup.send("✅ Сбор создан!")

@bot.tree.command(name="sbor_end", description="Завершить сбор и удалить голосовой канал")
async def sbor_end(interaction: discord.Interaction):
    if interaction.guild.id not in allowed_guild_ids:
        await interaction.response.send_message("❌ Команда недоступна на этом сервере.", ephemeral=True)
        return

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not any(r.id in allowed_role_ids for r in member.roles):
        await interaction.response.send_message("❌ У тебя нет прав для этой команды.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    channel_id = sbor_channels.get(interaction.guild.id)
    if not channel_id:
        await interaction.followup.send("❗ Канал 'сбор' не найден.")
        return

    channel = interaction.guild.get_channel(channel_id)
    if channel:
        await channel.delete()

    webhook = await interaction.channel.create_webhook(name="Сбор")
    await webhook.send(content="*Сбор окончен!*", username="Сбор", avatar_url=bot.user.avatar.url if bot.user.avatar else None)
    await webhook.delete()
    sbor_channels.pop(interaction.guild.id, None)
    await interaction.followup.send("✅ Сбор завершён.")

# Команда !ai
@bot.command()
async def ai(ctx, *, question: str):
    await ctx.send("Команда !ai временно отключена.")
    # КОД ОТКЛЮЧЕН
    # try:
        # headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
        # json_data = {
            # "model": "deepseek-chat",
            # "messages": [
                # {"role": "system", "content": "Ты — P-OS, искусственный интеллект сервера. Отвечай дружелюбно и профессионально."},
                # {"role": "user", "content": question}
            # ],
            # "max_tokens": 300,
            # "temperature": 0.7
        # }

        # response = requests.post(DEEPSEEK_API_URL, headers=headers, json=json_data).json()
        
        # исправлено: берем текст ответа напрямую
        # answer = "Нет ответа от ИИ."
        # if "choices" in response and len(response["choices"]) > 0:
            # answer = response["choices"][0]["message"]["content"]

        # embed = discord.Embed(
            # title="P-OS 🤖",
            # description=answer,
            # color=discord.Color.blurple()
        # )
        # embed.set_footer(text=f"Вопрос от {ctx.author}", icon_url=ctx.author.avatar.url)

        # await ctx.send(embed=embed)

    # except Exception as e:
        # await ctx.send(f"Произошла ошибка: {e}")

# -----------------------
# STOPREID (анти-спам)
# -----------------------
recent_messages = defaultdict(lambda: deque())

def message_key_for_spam(message: discord.Message):
    text = (message.content or "").strip()
    att_summary = [a.filename for a in message.attachments]
    return f"{text}||{'|'.join(att_summary)}"

async def handle_spam_if_needed(message: discord.Message):
    user_id = message.author.id
    key = message_key_for_spam(message)
    now = time.time()
    dq = recent_messages[user_id]
    dq.append((key, now, message.id, message.channel.id))

    while dq and now - dq[0][1] > SPAM_WINDOW_SECONDS:
        dq.popleft()

    count = sum(1 for k, _, _, _ in dq if k == key)
    if count < SPAM_DUPLICATES_THRESHOLD:
        return False

    to_delete = [mid for k0, _, mid, cid in dq if k0 == key and cid == message.channel.id]
    for mid in to_delete:
        try:
            msg = await message.channel.fetch_message(mid)
            if msg:
                await msg.delete()
        except Exception:
            pass

    reasons = [f"Спам одинаковыми сообщениями: {count} шт. за {SPAM_WINDOW_SECONDS} сек."]
    await apply_max_timeout(message.author, "Автомодерация: спам")
    await log_violation_with_evidence(message, "🚨 STOPREID: спам", reasons)

    try:
        dm = Embed(
            title="🚫 Обнаружен спам",
            description="Вы отправляли повторяющиеся сообщения. Выдано ограничение голоса на 24 часа.",
            color=Color.orange()
        )
        dm.add_field(name="Детали", value=reasons[0], inline=False)
        await message.author.send(embed=dm)
    except Exception:
        pass

    recent_messages[user_id].clear()
    return True


# -----------------------
# Жалобы: view + modal
# -----------------------
class RejectModal(Modal):
    def __init__(self, submitter: discord.Member, complaint_channel_id: int):
        super().__init__(title="Причина отклонения")
        self.submitter = submitter
        self.complaint_channel_id = complaint_channel_id
        self.reason = TextInput(label="Причина отклонения", style=discord.TextStyle.long, required=True, placeholder="Объясните почему отклоняете жалобу")
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        reason_text = self.reason.value
        guild = interaction.guild
        # найдем канал жалобы и соберём историю
        try:
            c = guild.get_channel(self.complaint_channel_id)
            history = []
            if c:
                async for m in c.history(limit=200, oldest_first=True):
                    history.append(f"{m.author.display_name}: {m.content}")
            history_text = "\n".join(history) if history else "(нет истории)"
        except Exception:
            history_text = "(не удалось получить историю)"

        # Сообщение автору (submitter)
        embed = Embed(title="❌ Ваша жалоба отклонена", color=Color.red())
        embed.add_field(name="Причина", value=reason_text, inline=False)
        embed.add_field(name="История жалобы", value=f"```{history_text[:1900]}```", inline=False)
        await safe_send_dm(self.submitter, embed)

        # Ответ админу
        await interaction.response.send_message("Отклонение отправлено автору. Канал жалобы будет удалён.", ephemeral=True)
        # удалим канал жалобы
        try:
            channel = guild.get_channel(self.complaint_channel_id)
            if channel:
                await channel.delete(reason=f"Жалоба отклонена админом {interaction.user}")
        except Exception:
            pass

class ComplaintView(View):
    def __init__(self, submitter: discord.Member, complaint_channel_id: int):
        super().__init__(timeout=None)
        self.submitter = submitter
        self.complaint_channel_id = complaint_channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Только админы (или пользователи с правом manage_guild / administrator)
        if interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message("❌ Только администраторы могут взаимодействовать.", ephemeral=True)
        return False

    @button(label="Одобрено", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Собираем историю жалобы
        guild = interaction.guild
        history = []
        channel = guild.get_channel(self.complaint_channel_id)
        if channel:
            async for m in channel.history(limit=200, oldest_first=True):
                history.append(f"{m.author.display_name}: {m.content}")
        history_text = "\n".join(history) if history else "(нет истории)"

        embed = Embed(title="✅ Ваша жалоба одобрена", color=Color.green())
        embed.add_field(name="История жалобы", value=f"```{history_text[:1900]}```", inline=False)
        await safe_send_dm(self.submitter, embed)

        await interaction.response.send_message("Жалоба одобрена, автор уведомлён.", ephemeral=True)
        # удалить канал жалобы
        try:
            if channel:
                await channel.delete(reason=f"Жалоба одобрена админом {interaction.user}")
        except Exception:
            pass

    @button(label="Отклонено", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Открываем modal, чтобы админ ввёл причину
        modal = RejectModal(submitter=self.submitter, complaint_channel_id=self.complaint_channel_id)
        await interaction.response.send_modal(modal)

# -----------------------
# ЕДИНЫЙ on_message: обрабатывает всё
# -----------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Проверка ссылок
    try:
        if await check_and_handle_urls(message):
            return
    except Exception as e:
        print(f"Ошибка проверки ссылок: {e}")
        return

    # сначала — STOPREID (анти-спам), независимо от канала
    try:
        if await handle_spam_if_needed(message):
            return
    except Exception:
        pass

    # Доп. автомодерация: реклама/скам в тексте + медиа-файлы
    text_reasons = detect_advertising_or_scam_text(message.content or "")
    attachment_reasons = detect_attachment_violations(message.attachments)
    moderation_reasons = text_reasons + attachment_reasons
    if moderation_reasons:
        try:
            await message.delete()
        except Exception:
            pass
        await apply_max_timeout(message.author, "Автомодерация: реклама/NSFW/вложения")
        await log_violation_with_evidence(message, "🚨 Автомодерация: нарушение контента", moderation_reasons)
        try:
            dm = Embed(
                title="🚫 Сообщение удалено",
                description="Обнаружены признаки рекламы/скама или нерелевантного медиа. Выдано ограничение голоса на 24 часа.",
                color=Color.red()
            )
            dm.add_field(name="Причины", value="\n".join(f"• {r}" for r in moderation_reasons)[:1024], inline=False)
            await message.author.send(embed=dm)
        except Exception:
            pass
        return
        
    # --- Авто-отписки: эмбед + ветка (thread) ---
    try:
        TARGET_CHANNELS = {
            1401268335798386810: "G.o.T",
            1416419420082933770: "C.E.S.U",
            1416417030520967199: "P.S.C"
        }

        TARGET_OUTPUT_CHANNEL = 1341377453049774160  # сюда всегда будет отправляться эмбед

        # ищем точное слово ОТПИСКИ (только заглавными, отдельно как слово)
        if message.channel.id in TARGET_CHANNELS and re.search(r'\bОТПИСКИ\b', message.content or ""):
            today = datetime.now().strftime("%d.%m.%Y")
            group = TARGET_CHANNELS[message.channel.id]

            if group == "P.S.C":
                title = f"Общее мероприятие [P.S.C] ({today}) - Отписки."
            else:
                title = f"Мероприятие [{group}] ({today}) - Отписки."

            embed = Embed(title=title, color=Color.from_rgb(255,255,255))

            # Отправляем эмбед в целевой канал и создаём под ним ветку с названием "Отписки"
            target_channel = bot.get_channel(TARGET_OUTPUT_CHANNEL)
            if target_channel:
                try:
                    sent = await target_channel.send(embed=embed)
                    try:
                        await sent.create_thread(name="Отписки")
                    except Exception:
                        # если нет прав создавать треды — молча пропускаем
                        pass
                except Exception as e:
                    print(f"Ошибка при отправке эмбеда/создании ветки: {e}")
    except Exception as e:
        print(f"Ошибка авто-отписок: {e}")
        
    # --- PSC EMBED СООБЩЕНИЯ ---
    if message.channel.id == PSC_CHANNEL_ID:
        # Условие: сообщение должно начинаться с "С ВЕБХУКОМ"
        content_strip = (message.content or "").strip()
        if content_strip.upper().startswith("С ВЕБХУКОМ"):
            # Удаляем исходное сообщение
            try:
                await message.delete()
            except Exception:
                pass

            # Пингуем роль в канале (вне эмбеда)
            guild = message.guild
            role_ping = guild.get_role(PING_ROLE_ID) if guild else None
            if role_ping:
                try:
                    await message.channel.send(role_ping.mention)
                except Exception:
                    pass

            # Уберём префикс из текста для эмбеда
            content_without_flag = content_strip[len("С ВЕБХУКОМ"):].strip()

            # Если есть вложение (картинка/гифка), используем её; иначе отправим эмбед без картинки
            file_to_send = None
            embed = Embed(description=content_without_flag or "(без текста)", color=Color.from_rgb(255,255,255))
            embed.set_footer(text=f"©Provision Security Complex | {datetime.now().strftime('%d.%m.%Y')}")
            if message.attachments:
                att = message.attachments[0]
                try:
                    os.makedirs("temp", exist_ok=True)
                    fname = f"temp/{uuid.uuid4().hex}-{att.filename}"
                    await att.save(fname)
                    file_to_send = discord.File(fname, filename=att.filename)
                    embed.set_image(url=f"attachment://{att.filename}")
                except Exception:
                    file_to_send = None

            try:
                if file_to_send:
                    await message.channel.send(embed=embed, file=file_to_send)
                    try:
                        os.remove(file_to_send.fp.name)
                    except Exception:
                        pass
                else:
                    await message.channel.send(embed=embed)
            except Exception:
                pass

            await bot.process_commands(message)
            return
        else:
            # не начиналось с флага — ничего не делаем
            await bot.process_commands(message)
            return

    # --- Жалобы: приём формы ---
    if message.channel.id == COMPLAINT_INPUT_CHANNEL:
        # Ожидаем форму: минимум 2 строки (1 и 2 обязательны), 3 строка необязательна
        lines = [ln.strip() for ln in (message.content or "").split("\n") if ln.strip()]
        if len(lines) < 2:
            # удаляем и отправляем автору в ЛС шаблон
            try:
                await message.delete()
            except Exception:
                pass
            template = "1. Никнейм нарушителя\n2. Суть нарушения\n3. Доказательства (по желанию)"
            em = Embed(title="❌ Неверная форма жалобы", description="В форме должно быть минимум 2 строки: никнейм и суть нарушения.", color=Color.red())
            em.add_field(name="Пример", value=f"```{template}```", inline=False)
            await safe_send_dm(message.author, em)
            await bot.process_commands(message)
            return

        # корректная форма — удаляем сообщение и создаём приватный канал
        try:
            await message.delete()
        except Exception:
            pass

        guild = message.guild
        # определяем категорию — используем категорию, где было сообщение
        category = message.channel.category

        # получаем порядковый номер жалобы в этой категории (в названии) — посчитаем существующие канал с префиксом "жалоба-"
        existing = [ch for ch in (category.channels if category else guild.channels) if ch.name.startswith("жалоба-")]
        index = len(existing) + 1
        channel_name = f"жалоба-{index}"

        # настройки overwrites: скрываем для @everyone, показываем автору и всем admin ролям
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
            message.author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        }
        # даём доступ ролям, у которых есть админские права
        for role in guild.roles:
            try:
                if role.permissions.administrator or role.permissions.manage_guild:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            except Exception:
                pass

        # создаём канал
        try:
            complaint_chan = await guild.create_text_channel(channel_name, overwrites=overwrites, category=category, reason=f"Новая жалоба от {message.author}")
        except Exception:
            # попытка создать канал в другом месте (если нет категории)
            try:
                complaint_chan = await guild.create_text_channel(channel_name, overwrites=overwrites, reason=f"Новая жалоба от {message.author}")
            except Exception:
                complaint_chan = None

        # формируем эмбед с жалобой и кнопками
        embed = Embed(title="📢 Новая жалоба", color=Color.blue())
        embed.add_field(name="Отправитель", value=f"{message.author.mention} (ID: {message.author.id})", inline=False)
        # вставим всё отправленное (в виде кода, чтобы не ломать)
        full_text = "\n".join(lines)
        embed.add_field(name="Жалоба", value=f"```{full_text[:1900]}```", inline=False)
        embed.set_footer(text=f"ID жалобы: {index} | {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        # упоминание роли уведомления
        notify_role = guild.get_role(COMPLAINT_NOTIFY_ROLE)
        try:
            # отправляем сообщение в канал жалобы: пингуем роль отдельно чтобы она уведомилась
            if complaint_chan:
                if notify_role:
                    await complaint_chan.send(notify_role.mention)
                view = ComplaintView(submitter=message.author, complaint_channel_id=complaint_chan.id)
                await complaint_chan.send(embed=embed, view=view)

                if message.attachments:
                    files = []
                    for att in message.attachments[:5]:
                        try:
                            files.append(await att.to_file())
                        except Exception:
                            pass
                    if files:
                        await complaint_chan.send(content="📎 Приложенные доказательства от автора жалобы:", files=files)
        except Exception:
            pass

        await bot.process_commands(message)
        return

    # --- Обработка формы вступления (единый отряд TAS) ---
    if message.channel.id == FORM_CHANNEL_ID:
        template = "1. Ваш roblox никнейм (НЕ ДИСПЛЕЙ)\n2. Ваш Discord ник\n3. tas"
        lines = [line.strip() for line in (message.content or "").strip().split("\n") if line.strip()]
        if len(lines) != 3:
            try:
                await message.reply(embed=Embed(title="❌ Неверный шаблон", description="Форма должна содержать 3 строки.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            except Exception:
                pass
            await bot.process_commands(message)
            return

        user_line, discord_nick_line, choice_line = lines
        keyword = extract_clean_keyword(choice_line)

        if keyword != "tas":
            try:
                await message.reply(embed=Embed(title="❌ Неизвестный отряд", description="Третий пункт должен содержать только **tas**.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            except Exception:
                pass
            await bot.process_commands(message)
            return

        target_channel = message.guild.get_channel(TAS_CHANNEL_ID)
        if not target_channel:
            try:
                await message.reply("❌ Ошибка конфигурации: канал TAS не найден.")
            except Exception:
                pass
            await bot.process_commands(message)
            return

        risk_flags = assess_applicant_risk(user_line, discord_nick_line, message.author)
        risk_text = "\n".join(f"⚠️ {flag}" for flag in risk_flags) if risk_flags else "✅ Явных рисков не обнаружено"

        embed = Embed(
            title="📋 Подтверждение вступления в TAS",
            description=(
                f"{message.author.mention} хочет вступить в отряд **TAS**\n"
                f"Roblox ник: `{user_line}`\n"
                f"Discord ник: `{discord_nick_line}`"
            ),
            color=Color.blue()
        )
        embed.add_field(name="Автопроверка кандидата", value=risk_text[:1024], inline=False)
        embed.set_footer(text=f"ID пользователя: {message.author.id} | {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")

        view = ConfirmView(
            TAS_REVIEWER_ROLE_IDS,
            target_message=message,
            squad_name="TAS",
            role_ids=TAS_ROLE_REWARDS,
            target_user_id=message.author.id
        )

        mentions = []
        for rid in TAS_REVIEWER_ROLE_IDS:
            role = message.guild.get_role(rid)
            if role:
                mentions.append(role.mention)

        try:
            if mentions:
                await target_channel.send(content=" ".join(mentions), embed=embed, view=view)
            else:
                await target_channel.send(embed=embed, view=view)
        except Exception:
            pass

        await bot.process_commands(message)
        return

    # --- Обработка наказаний (форма по id, выговоры/страйки) ---
    if message.channel.id == form_channel_id:
        template = (
            "Никнейм: Robloxer228\n"
            "Дискорд айди: 1234567890\n"
            "Наказание: 1 выговор / 2 выговора / 1 страйк / 2 страйка\n"
            "Причина: причина наказания\n"
            "Док-ва: (по желанию)"
        )

        lines = [line.strip() for line in (message.content or "").strip().split("\n") if line.strip()]
        if len(lines) < 4 or len(lines) > 5:
            try:
                await message.reply(embed=Embed(title="❌ Ошибка", description="Форма должна содержать 4 или 5 строк.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            except Exception:
                pass
            await bot.process_commands(message)
            return

        try:
            nickname = lines[0].split(":", 1)[1].strip()
            user_id = int(lines[1].split(":", 1)[1].strip())
            punishment = lines[2].split(":", 1)[1].strip().lower()
            reason = lines[3].split(":", 1)[1].strip()
        except Exception:
            try:
                await message.reply(embed=Embed(title="❌ Ошибка в шаблоне", description="Проверь правильность полей (особенно Discord ID)", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            except Exception:
                pass
            await bot.process_commands(message)
            return

        member = message.guild.get_member(user_id)
        if not member:
            try:
                await message.reply("❌ Пользователь с таким ID не найден на сервере.")
            except Exception:
                pass
            await bot.process_commands(message)
            return

        roles = member.roles
        log = message.guild.get_channel(log_channel_id)

        async def log_action(text):
            if log:
                await log.send(embed=Embed(title="📋 Лог наказаний", description=text, color=Color.orange()))

        async def apply_roles(to_add, to_remove):
            for r in to_remove:
                if r in roles:
                    try:
                        await member.remove_roles(r)
                    except Exception:
                        pass
            for r in to_add:
                if r not in roles:
                    try:
                        await member.add_roles(r)
                    except Exception:
                        pass

        punish_1 = message.guild.get_role(punishment_roles["1 выговор"])
        punish_2 = message.guild.get_role(punishment_roles["2 выговора"])
        strike_1 = message.guild.get_role(punishment_roles["1 страйк"])
        strike_2 = message.guild.get_role(punishment_roles["2 страйка"])

        # Логика наказаний — как было
        if all(r in roles for r in [punish_1, punish_2, strike_1, strike_2]):
            if squad_roles["got_base"] in [r.id for r in roles]:
                notify = message.guild.get_role(squad_roles["got_notify"])
            elif squad_roles["cesu_base"] in [r.id for r in roles]:
                notify = message.guild.get_role(squad_roles["cesu_notify"])
            else:
                notify = None
            if notify:
                await log_action(f"{notify.mention}\nСотрудник {member.mention} получил **максимальное количество наказаний** и подлежит **увольнению**.")
            await bot.process_commands(message)
            return

        if punishment == "1 выговор":
            if punish_1 in roles and punish_2 in roles:
                await apply_roles([strike_1], [punish_1, punish_2])
                await log_action(f"{member.mention} получил 1 страйк (2 выговора заменены).")
            elif punish_1 in roles:
                await apply_roles([punish_2], [])
                await log_action(f"{member.mention} получил второй выговор.")
            else:
                await apply_roles([punish_1], [])
                await log_action(f"{member.mention} получил первый выговор.")
        elif punishment == "2 выговора":
            if punish_1 in roles and punish_2 in roles:
                await apply_roles([strike_1], [punish_1, punish_2])
                await log_action(f"{member.mention} получил 1 страйк (2 выговора заменены).")
            elif punish_1 in roles:
                await apply_roles([strike_1], [punish_1])
                await log_action(f"{member.mention} получил 1 страйк (1 выговор заменён).")
            else:
                await apply_roles([punish_1, punish_2], [])
                await log_action(f"{member.mention} получил 2 выговора.")
        elif punishment == "1 страйк":
            if strike_1 in roles and strike_2 in roles:
                if squad_roles["got_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["got_notify"])
                elif squad_roles["cesu_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["cesu_notify"])
                else:
                    notify = None
                if notify:
                    await log_action(f"{notify.mention}\nСотрудник {member.mention} получил 3-й страйк. Подлежит увольнению.")
            elif strike_1 in roles:
                await apply_roles([strike_2], [])
                await log_action(f"{member.mention} получил второй страйк.")
            else:
                await apply_roles([strike_1], [])
                await log_action(f"{member.mention} получил первый страйк.")
        elif punishment == "2 страйка":
            if strike_1 in roles or strike_2 in roles:
                if squad_roles["got_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["got_notify"])
                elif squad_roles["cesu_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["cesu_notify"])
                else:
                    notify = None
                if notify:
                    await log_action(f"{notify.mention}\nСотрудник {member.mention} уже имеет страйки и получил ещё. Подлежит увольнению.")
            else:
                await apply_roles([strike_1, strike_2], [])
                await log_action(f"{member.mention} получил 2 страйка.")
        else:
            try:
                await message.reply(embed=Embed(title="❌ Неизвестное наказание", description="Допустимые значения: `1 выговор`, `2 выговора`, `1 страйк`, `2 страйка`.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
            except Exception:
                pass

        await bot.process_commands(message)
        return

    # Если ни одно условие не сработало — пропускаем к прочим командам
    await bot.process_commands(message)

@bot.event
async def on_member_join(member: discord.Member):
    # --- Приветствие ---
    try:
        channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title="## Запись в дата-базу P - O.S…",
                description=(
                    f"————————————\n"
                    f"```\n"
                    f"Приветствую, {member.name}!\n"
                    f"Добро пожаловать на связанной центр фракции P.S.C!\n"
                    f"Вы записаны в базу хранения.\n"
                    f"```\n\n"
                    f"> **Ознакомьтесь с "
                    f"<#1340596281986383912>, "
                    f"там вы найдёте нужную вам информацию!**"
                ),
                color=discord.Color.green()
            )
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Ошибка приветствия on_member_join: {e}")

    # --- Выдача ролей всем новым игрокам ---
    try:
        new_roles_ids = [
            1341100157902651514,
            1349669624395858012,
            1341202442942939207,
            1341168051269275718,
            1341164841649574090,
            1341100388715335730,
            1392396942079954985
        ]
        for rid in new_roles_ids:
            role = member.guild.get_role(rid)
            if role:
                await member.add_roles(role, reason="Выдача ролей новым игрокам")
    except Exception as e:
        print(f"Ошибка выдачи ролей on_member_join: {e}")

@bot.event
async def on_member_remove(member: discord.Member):
    try:
        channel = member.guild.get_channel(GOODBYE_CHANNEL_ID)
        if channel:
            embed = Embed(
                title="## Выписываю из базы данных…",
                description=(
                    f"```\n"
                    f"Желаем удачи, {member.name}!\n"
                    f"Вы выписаны из базы данных.\n"
                    f"Ждём вас снова у нас!\n"
                    f"```"
                ),
                color=Color.red()
            )
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Ошибка on_member_remove: {e}")

# -----------------------
# on_ready: синхронизация команд
# -----------------------
@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user} (id: {bot.user.id})")
    runtime = collect_runtime_health()
    missing_optional = [k for k, available in runtime.items() if not available and k != "DISCORD_TOKEN"]
    if missing_optional:
        print(f"⚠️ Не настроены опциональные ключи: {', '.join(missing_optional)}")

    for guild_id in allowed_guild_ids:
        try:
            guild = discord.Object(id=guild_id)
            await bot.tree.sync(guild=guild)
            print(f"✅ Команды синхронизированы с сервером {guild_id}")
        except Exception as e:
            print(f"❌ Ошибка при синхронизации: {e}")

# -----------------------
# Запуск
# -----------------------
if __name__ == "__main__":
    token, token_source = resolve_discord_token()
    if not token:
        print("ERROR: Discord token not set. Configure one of env vars: DISCORD_TOKEN / BOT_TOKEN / TOKEN")
    elif not looks_like_discord_token(token):
        print(f"ERROR: Discord token from {token_source} has invalid format. Check Railway variable value (no quotes/prefix).")
    else:
        try:
            print(f"ℹ️ Использую токен из переменной: {token_source}")
            bot.run(token)
        except discord.errors.LoginFailure:
            print(
                f"ERROR: Discord login failed (401 Unauthorized). Проверь токен в Railway ({token_source}) и регенерируй его в Discord Developer Portal."
            )
