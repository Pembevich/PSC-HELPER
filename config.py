import os
import re

# --- Ключи ---
VIRUSTOTAL_KEY = os.getenv("VIRUSTOTAL_KEY")
GOOGLE_SAFEBROWSING_KEY = os.getenv("GOOGLE_SAFEBROWSING_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat"
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_API_URL = os.getenv(
    "NVIDIA_API_URL",
    "https://integrate.api.nvidia.com/v1/chat/completions"
)
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct")

# --- Логи ---
LOG_CATEGORY_ID = int(os.getenv("LOG_CATEGORY_ID", "0") or 0)
LOG_CATEGORY_NAME = os.getenv("LOG_CATEGORY_NAME", "логи")

# -----------------------
# Фильтрация ссылок
# -----------------------
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

SUSPICIOUS_DOMAINS = {
    "pornhub.com", "xvideos.com", "xnxx.com", "xhamster.com", "redtube.com",
    "youporn.com", "tube8.com", "tnaflix.com", "spankwire.com", "brazzers.com",
    "onlyfans.com", "cams.com", "adultfriendfinder.com", "cam4.com",
    "1xbet.com", "bet365.com", "betfair.com", "stake.com", "pokerstars.com",
    "betway.com", "bovada.lv", "draftkings.com", "fanduel.com", "casino.com",
    "free-robux.com", "robux-free.com", "nitro.gifts", "verify-nitro.com",
    "discord-nitro.com", "discordgift.com", "discord-gift.com", "nitroclaim.xyz",
}

SUSPICIOUS_PATH_KEYWORDS = [
    "claim", "free", "giveaway", "get-nitro", "nitro", "free-robux",
    "verify", "verification", "password", "reset", "voucher", "coupon", "earn"
]

WHITELIST_DOMAINS = {
    "discord.com", "discord.gg", "youtube.com", "roblox.com", "google.com", "github.com"
}

# регулярка для поиска ссылок
URL_REGEX = re.compile(r"(https?://[^\s<>()]+)")

_VT_CACHE_TTL = 60 * 60  # 1 час

# -----------------------
# Каналы / роли
# -----------------------
WELCOME_CHANNEL_ID = 1351880905936867328
GOODBYE_CHANNEL_ID = 1351880978808963073

allowed_role_ids = [1340596390614532127, 1341204231419461695]
allowed_guild_ids = [1340594372596469872]

FORM_CHANNEL_ID = 1340996239113850971

TAC_CHANNEL_ID = 1394635110665556009
TAC_REVIEWER_ROLE_IDS = [1341041194733670401, 1341040607728107591, 1341040703551307846]
TAC_ROLE_REWARDS = [1341040784723411017, 1341040871562285066, 1341100562783014965, 1341039967555551333]

VOICE_TIMEOUT_HOURS = 24
VIOLATION_ATTACHMENT_LIMIT = 3
NSFW_FILENAME_KEYWORDS = ["porn", "sex", "xxx", "nsfw", "erotic", "nud", "18+"]
AD_FILENAME_KEYWORDS = ["casino", "bet", "free", "nitro", "robux", "giveaway", "promo", "advert"]
AD_TEXT_KEYWORDS = ["подпишись", "промокод", "ставки", "казино", "розыгрыш", "nitro", "robux", "бонус"]
SUSPICIOUS_INVITE_PATTERNS = ["t.me/", "vk.com/"]
ALLOWED_DISCORD_INVITE_PATTERNS = ["discord.gg/", "discord.com/invite/"]

PSC_CHANNEL_ID = 1416417030520967199
PING_ROLE_ID = 1341168051269275718

SPAM_WINDOW_SECONDS = 10
SPAM_DUPLICATES_THRESHOLD = 4
SPAM_LOG_CHANNEL = 1347412933986226256
MUTE_ROLE_ID = 1426552449874919624

COMPLAINT_INPUT_CHANNEL = 1404977876184334407
COMPLAINT_NOTIFY_ROLE = 1341203508606533763

# Лог канал (fallback)
log_channel_id = 1392125177399218186

# Канал для форм наказаний
form_channel_id = 1349725568371003392

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

TARGET_CHANNELS = {
    1401268335798386810: "G.o.T",
    1416419420082933770: "C.E.S.U",
    1416417030520967199: "P.S.C"
}

TARGET_OUTPUT_CHANNEL = 1341377453049774160

NEW_MEMBER_ROLE_IDS = [
    1341100157902651514,
    1349669624395858012,
    1341202442942939207,
    1341168051269275718,
    1341164841649574090,
    1341100388715335730,
    1392396942079954985
]
