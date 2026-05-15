from __future__ import annotations

"""
OTP Monitor — three independent monitors run in parallel:

1. OTPMonitor  (SMS Hadi — sesskey-based auth)
   Logs into http://smshadi.net, fetches individual SMS records via
   /agent/SMSCDRStats DataTables AJAX (sesskey in URL).
   Column order (fg=0):
     [0] datetime  [1] range_name  [2] number  [3] client/website
     [4] cli       [5] sms_body    [6] currency [7] my_payout

2. ClientPanelMonitor  (generic — cookie-based auth, /client/ path)
   Used for both Konekta Premium and MSI SMS panels.
   Logs in, fetches /client/res/data_smscdr.php — no sesskey needed.
   Column order (fg=0):
     [0] datetime  [1] range_name  [2] number  [3] cli (sender)
     [4] sms_body  [5] currency    [6] my_payout
   Website is detected from SMS body text.

   Instances:
     • konekta_monitor  → https://konektapremium.net  (login at /sign-in)
     • msi_sms_monitor  → http://145.239.130.45/ints  (login at /login)
"""

import asyncio
import concurrent.futures
import hashlib
import html as _html
import logging
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests

# ── Login retry config (Fresh Session Reload) ────────────────────────────────
# When a panel login fails, the bot will immediately drop the session,
# build a brand-new session, GET the login page again, solve the fresh captcha
# and POST credentials. This mimics a manual page-reload retry in a browser.
_LOGIN_FAST_RETRIES = 3   # number of fast in-call retries before giving up
_LOGIN_RETRY_DELAY  = 2   # seconds between fast retries

# ── Dedicated thread pool for OTP monitors ────────────────────────────────────
# Keeps all OTP HTTP I/O and DB calls off the main bot-handler thread pool
# so 20 000 concurrent users never compete with background panel polling.
_OTP_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=24,
    thread_name_prefix="otp-monitor",
)


async def _otp_thread(func, *args):
    """Run *func* in the dedicated OTP executor (not the shared bot pool)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_OTP_EXECUTOR, func, *args)

from config import (
    GROUP_CHAT_ID,
    SMS_HADI_BASE,
    SMS_HADI_LOGIN_URL,
    SMS_HADI_SIGNIN_URL,
    SMS_HADI_AJAX_URL,
    SMS_HADI_USERNAME,
    SMS_HADI_PASSWORD,
    KONEKTA_BASE,
    KONEKTA_LOGIN_URL,
    KONEKTA_SIGNIN_URL,
    KONEKTA_USERNAME,
    KONEKTA_PASSWORD,
    MSI_SMS_BASE,
    MSI_SMS_LOGIN_URL,
    MSI_SMS_SIGNIN_URL,
    MSI_SMS_USERNAME,
    MSI_SMS_PASSWORD,
    SMS_MONITOR_INTERVAL,
    NUMBER_PANEL_BASE,
    NUMBER_PANEL_LOGIN_URL,
    NUMBER_PANEL_SIGNIN_URL,
    NUMBER_PANEL_STATS_URL,
    NUMBER_PANEL_AJAX_URL,
    NUMBER_PANEL_USERNAME,
    NUMBER_PANEL_PASSWORD,
    NUMBER_PANEL_INTERVAL,
    PURPLE_SMS_BASE,
    PURPLE_SMS_LOGIN_URL,
    PURPLE_SMS_SIGNIN_URL,
    PURPLE_SMS_STATS_URL,
    PURPLE_SMS_AJAX_URL,
    PURPLE_SMS_USERNAME,
    PURPLE_SMS_PASSWORD,
    PROOF_SMS_BASE,
    PROOF_SMS_LOGIN_URL,
    PROOF_SMS_SIGNIN_URL,
    PROOF_SMS_USERNAME,
    PROOF_SMS_PASSWORD,
    LAMIX_SMS_BASE,
    LAMIX_SMS_LOGIN_URL,
    LAMIX_SMS_SIGNIN_URL,
    LAMIX_SMS_USERNAME,
    LAMIX_SMS_PASSWORD,
    SEVEN1TEL_BASE,
    SEVEN1TEL_LOGIN_URL,
    SEVEN1TEL_SIGNIN_URL,
    SEVEN1TEL_USERNAME,
    SEVEN1TEL_PASSWORD,
    MAIT_SMS_BASE,
    MAIT_SMS_LOGIN_URL,
    MAIT_SMS_SIGNIN_URL,
    MAIT_SMS_USERNAME,
    MAIT_SMS_PASSWORD,
    ZENTO_SMS_BASE,
    ZENTO_SMS_LOGIN_URL,
    ZENTO_SMS_SIGNIN_URL,
    ZENTO_SMS_USERNAME,
    ZENTO_SMS_PASSWORD,
    WOLF_SMS_BASE,
    WOLF_SMS_LOGIN_URL,
    WOLF_SMS_SIGNIN_URL,
    WOLF_SMS_STATS_URL,
    WOLF_SMS_AJAX_URL,
    WOLF_SMS_USERNAME,
    WOLF_SMS_PASSWORD,
    WOLF_SMS_INTERVAL,
    SHARK_SMS_BASE,
    SHARK_SMS_LOGIN_URL,
    SHARK_SMS_SIGNIN_URL,
    SHARK_SMS_STATS_URL,
    SHARK_SMS_AJAX_URL,
    SHARK_SMS_USERNAME,
    SHARK_SMS_PASSWORD,
    SHARK_SMS_INTERVAL,
    SMS_HADI2_USERNAME,
    SMS_HADI2_PASSWORD,
    SMS_HADI2_LOGIN_URL,
    SMS_HADI2_SIGNIN_URL,
    SMS_HADI2_STATS_URL,
    SMS_HADI2_AJAX_URL,
)

logger = logging.getLogger(__name__)


async def _notify_admins_login_fail(bot, panel_name: str):
    """Send a login-failure alert to all admin users that have a known user_id."""
    try:
        from database import _get_all_admins_with_details
        admins = _get_all_admins_with_details()
        for admin in admins:
            uid = admin.get("user_id")
            if uid:
                try:
                    await bot.send_message(
                        chat_id=uid,
                        text=(
                            f"⚠️ *Panel Login Failed*\n\n"
                            f"🖥️ *{panel_name}* could not log in.\n"
                            "The bot will keep retrying automatically.\n\n"
                            "_Check credentials or panel status._"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
    except Exception:
        pass


async def _notify_admins_login_success(bot, panel_name: str):
    """Send a login-success alert to all admins (after a previous failure)."""
    try:
        from database import _get_all_admins_with_details
        admins = _get_all_admins_with_details()
        for admin in admins:
            uid = admin.get("user_id")
            if uid:
                try:
                    await bot.send_message(
                        chat_id=uid,
                        text=(
                            f"✅ *Panel Login Successful*\n\n"
                            f"🖥️ *{panel_name}* has successfully logged in.\n"
                            "The bot has started receiving SMS/OTP from this panel."
                        ),
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
    except Exception:
        pass


# ── Constants ─────────────────────────────────────────────────────────────────

SMS_HADI_REPORTS_URL = f"{SMS_HADI_BASE}/agent/SMSCDRStats"
SMS_HADI_AJAX_BASE   = f"{SMS_HADI_BASE}/agent/res/data_smscdr.php"

_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)

# ── Shared helpers ────────────────────────────────────────────────────────────

_SKIP_WORDS = {
    's', 'mix', 'smsc', 'route', 'pack', 'pool', 'num', 'number',
    'sms', 'gsm', 'virtual', 'did',
}

# Telecom operator / network suffix words — stop country extraction here
_OPERATOR_STOP_WORDS = {
    'mtn', 'grand', 'mobile', 'telecom', 'cellular', 'network', 'wireless',
    'communications', 'orange', 'airtel', 'vodafone', 'zain', 'ooredoo',
    'etisalat', 'tmobile', 't-mobile', 'telecel', 'tigo', 'digicel', 'glo',
    'claro', 'movistar', 'entel', 'nextel', 'turkcell', 'telefonica',
    'beeline', 'megafon', 'safaricom', 'grameenphone', 'robi', 'banglalink',
    'teletalk', 'unitel', 'africell', 'expresso', 'moov', 'libyana',
    'almadar', 'sudatel', 'sudani', 'jawwal', 'palestel', 'premium',
    'standard', 'national', 'international', 'local', 'direct', 'special',
    'geo', 'geo2', 'tier', 'basic', 'plus', 'pro', 'fixed', 'landline',
    'voip', 'tollfree', 'toll', 'free', 'shared', 'service',
    'turk', 'telekom', 'mts', 'vimpelcom', 'canal', 'nine', 'xl',
}

# Known multi-word countries (longest first so we match greedily)
_MULTI_WORD_COUNTRIES = [
    'United Arab Emirates', 'United States', 'United Kingdom',
    'South Africa', 'South Korea', 'South Sudan', 'North Korea',
    'Saudi Arabia', 'Sri Lanka', 'New Zealand', 'Costa Rica',
    'Puerto Rico', 'El Salvador', 'Papua New Guinea', 'Burkina Faso',
    'Sierra Leone', 'Ivory Coast', 'Trinidad And Tobago',
    'Bosnia And Herzegovina', 'Dominican Republic', 'Czech Republic',
]

_OTP_PATTERNS = [
    re.compile(r'\b(\d{4,8})\s+is your (?:verification |OTP |one.time )?code', re.I),
    re.compile(r'(?:code|otp|password|pin|token)[^\d]{0,20}(\d{4,8})', re.I),
    re.compile(r'(?:verification|verify)[^\d]{0,30}(\d{4,8})', re.I),
    re.compile(r'#(\d{4,8})', re.I),
    re.compile(r'\b(\d{6})\b'),
    re.compile(r'\b(\d{4})\b'),
]

_WEBSITE_PATTERNS = [
    (re.compile(r'\b(facebook|fb)\b', re.I),          'Facebook'),
    (re.compile(r'\b(instagram|ig)\b', re.I),          'Instagram'),
    (re.compile(r'\b(whatsapp)\b', re.I),              'WhatsApp'),
    (re.compile(r'\b(telegram)\b', re.I),              'Telegram'),
    (re.compile(r'\b(google|gmail|youtube)\b', re.I),  'Google'),
    (re.compile(r'\b(twitter|x\.com)\b', re.I),        'Twitter/X'),
    (re.compile(r'\b(tiktok)\b', re.I),                'TikTok'),
    (re.compile(r'\b(snapchat)\b', re.I),              'Snapchat'),
    (re.compile(r'\b(amazon)\b', re.I),                'Amazon'),
    (re.compile(r'\b(netflix)\b', re.I),               'Netflix'),
    (re.compile(r'\b(microsoft|outlook|hotmail)\b', re.I), 'Microsoft'),
    (re.compile(r'\b(apple|icloud|itunes)\b', re.I),   'Apple'),
    (re.compile(r'\b(paypal)\b', re.I),                'PayPal'),
    (re.compile(r'\b(uber)\b', re.I),                  'Uber'),
    (re.compile(r'\b(linkedin)\b', re.I),              'LinkedIn'),
    (re.compile(r'\b(binance)\b', re.I),               'Binance'),
    (re.compile(r'\b(coinbase)\b', re.I),              'Coinbase'),
    (re.compile(r'\b(discord)\b', re.I),               'Discord'),
    (re.compile(r'\b(spotify)\b', re.I),               'Spotify'),
    (re.compile(r'\b(shopify)\b', re.I),               'Shopify'),
    (re.compile(r'\b(alibaba|aliexpress)\b', re.I),    'Alibaba'),
    (re.compile(r'\b(lazada)\b', re.I),                'Lazada'),
    (re.compile(r'\b(grab)\b', re.I),                  'Grab'),
    (re.compile(r'\b(airbnb)\b', re.I),                'Airbnb'),
    (re.compile(r'\b(ebay)\b', re.I),                  'eBay'),
]


def _solve_captcha(html: str):
    for op_re, fn in [
        # "What is X + Y" style (SMS Hadi, Number Panel, etc.)
        (re.compile(r'What is\s+(\d+)\s*\+\s*(\d+)', re.I), lambda a, b: a + b),
        (re.compile(r'What is\s+(\d+)\s*-\s*(\d+)',  re.I), lambda a, b: a - b),
        (re.compile(r'What is\s+(\d+)\s*\*\s*(\d+)', re.I), lambda a, b: a * b),
        (re.compile(r'What is\s+(\d+)\s*/\s*(\d+)',  re.I), lambda a, b: a // b),
        # "X + Y = ?" style (Purple SMS, etc.)
        (re.compile(r'(\d+)\s*\+\s*(\d+)\s*=\s*\?'), lambda a, b: a + b),
        (re.compile(r'(\d+)\s*-\s*(\d+)\s*=\s*\?'),  lambda a, b: a - b),
        (re.compile(r'(\d+)\s*\*\s*(\d+)\s*=\s*\?'), lambda a, b: a * b),
        (re.compile(r'(\d+)\s*/\s*(\d+)\s*=\s*\?'),  lambda a, b: a // b),
    ]:
        m = op_re.search(html)
        if m:
            return str(fn(int(m.group(1)), int(m.group(2))))
    return None


# ── Country dial-code → country name (used as fallback when panel
#    does not provide a range_name from which to extract a country) ──
_COUNTRY_DIAL_CODES: dict[str, str] = {
    # 1-digit
    "1": "United States", "7": "Russia",
    # 2-digit
    "20": "Egypt", "27": "South Africa", "30": "Greece", "31": "Netherlands",
    "32": "Belgium", "33": "France", "34": "Spain", "36": "Hungary",
    "39": "Italy", "40": "Romania", "41": "Switzerland", "43": "Austria",
    "44": "United Kingdom", "45": "Denmark", "46": "Sweden", "47": "Norway",
    "48": "Poland", "49": "Germany", "51": "Peru", "52": "Mexico",
    "53": "Cuba", "54": "Argentina", "55": "Brazil", "56": "Chile",
    "57": "Colombia", "58": "Venezuela", "60": "Malaysia", "61": "Australia",
    "62": "Indonesia", "63": "Philippines", "64": "New Zealand",
    "65": "Singapore", "66": "Thailand", "81": "Japan", "82": "South Korea",
    "84": "Vietnam", "86": "China", "90": "Turkey", "91": "India",
    "92": "Pakistan", "93": "Afghanistan", "94": "Sri Lanka", "95": "Myanmar",
    "98": "Iran",
    # 3-digit
    "211": "South Sudan", "212": "Morocco", "213": "Algeria", "216": "Tunisia",
    "218": "Libya", "220": "Gambia", "221": "Senegal", "222": "Mauritania",
    "223": "Mali", "224": "Guinea", "225": "Côte d'Ivoire",
    "226": "Burkina Faso", "227": "Niger", "228": "Togo", "229": "Benin",
    "230": "Mauritius", "231": "Liberia", "232": "Sierra Leone", "233": "Ghana",
    "234": "Nigeria", "235": "Chad", "236": "Central African Republic",
    "237": "Cameroon", "238": "Cape Verde", "239": "São Tomé",
    "240": "Equatorial Guinea", "241": "Gabon", "242": "Congo",
    "243": "DR Congo", "244": "Angola", "245": "Guinea-Bissau",
    "248": "Seychelles", "249": "Sudan", "250": "Rwanda", "251": "Ethiopia",
    "252": "Somalia", "253": "Djibouti", "254": "Kenya", "255": "Tanzania",
    "256": "Uganda", "257": "Burundi", "258": "Mozambique", "260": "Zambia",
    "261": "Madagascar", "262": "Réunion", "263": "Zimbabwe", "264": "Namibia",
    "265": "Malawi", "266": "Lesotho", "267": "Botswana", "268": "Eswatini",
    "269": "Comoros", "291": "Eritrea", "297": "Aruba", "298": "Faroe Islands",
    "299": "Greenland", "350": "Gibraltar", "351": "Portugal",
    "352": "Luxembourg", "353": "Ireland", "354": "Iceland", "355": "Albania",
    "356": "Malta", "357": "Cyprus", "358": "Finland", "359": "Bulgaria",
    "370": "Lithuania", "371": "Latvia", "372": "Estonia", "373": "Moldova",
    "374": "Armenia", "375": "Belarus", "376": "Andorra", "377": "Monaco",
    "378": "San Marino", "380": "Ukraine", "381": "Serbia", "382": "Montenegro",
    "383": "Kosovo", "385": "Croatia", "386": "Slovenia",
    "387": "Bosnia and Herzegovina", "389": "North Macedonia",
    "420": "Czech Republic", "421": "Slovakia", "423": "Liechtenstein",
    "500": "Falkland Islands", "501": "Belize", "502": "Guatemala",
    "503": "El Salvador", "504": "Honduras", "505": "Nicaragua",
    "506": "Costa Rica", "507": "Panama", "509": "Haiti", "591": "Bolivia",
    "592": "Guyana", "593": "Ecuador", "594": "French Guiana",
    "595": "Paraguay", "596": "Martinique", "597": "Suriname", "598": "Uruguay",
    "599": "Curaçao", "670": "East Timor", "673": "Brunei", "674": "Nauru",
    "675": "Papua New Guinea", "676": "Tonga", "677": "Solomon Islands",
    "678": "Vanuatu", "679": "Fiji", "680": "Palau", "682": "Cook Islands",
    "685": "Samoa", "686": "Kiribati", "687": "New Caledonia", "688": "Tuvalu",
    "689": "French Polynesia", "691": "Micronesia", "692": "Marshall Islands",
    "850": "North Korea", "852": "Hong Kong", "853": "Macau", "855": "Cambodia",
    "856": "Laos", "880": "Bangladesh", "886": "Taiwan", "960": "Maldives",
    "961": "Lebanon", "962": "Jordan", "963": "Syria", "964": "Iraq",
    "965": "Kuwait", "966": "Saudi Arabia", "967": "Yemen", "968": "Oman",
    "970": "Palestine", "971": "United Arab Emirates", "972": "Israel",
    "973": "Bahrain", "974": "Qatar", "975": "Bhutan", "976": "Mongolia",
    "977": "Nepal", "992": "Tajikistan", "993": "Turkmenistan",
    "994": "Azerbaijan", "995": "Georgia", "996": "Kyrgyzstan",
    "998": "Uzbekistan",
}

# ── Dial-code → ISO 2-letter country code ─────────────────────────────────────
_DIAL_CODE_TO_ISO: dict[str, str] = {
    "1": "US", "7": "RU",
    "20": "EG", "27": "ZA", "30": "GR", "31": "NL",
    "32": "BE", "33": "FR", "34": "ES", "36": "HU",
    "39": "IT", "40": "RO", "41": "CH", "43": "AT",
    "44": "GB", "45": "DK", "46": "SE", "47": "NO",
    "48": "PL", "49": "DE", "51": "PE", "52": "MX",
    "53": "CU", "54": "AR", "55": "BR", "56": "CL",
    "57": "CO", "58": "VE", "60": "MY", "61": "AU",
    "62": "ID", "63": "PH", "64": "NZ",
    "65": "SG", "66": "TH", "81": "JP", "82": "KR",
    "84": "VN", "86": "CN", "90": "TR", "91": "IN",
    "92": "PK", "93": "AF", "94": "LK", "95": "MM",
    "98": "IR",
    "211": "SS", "212": "MA", "213": "DZ", "216": "TN",
    "218": "LY", "220": "GM", "221": "SN", "222": "MR",
    "223": "ML", "224": "GN", "225": "CI",
    "226": "BF", "227": "NE", "228": "TG", "229": "BJ",
    "230": "MU", "231": "LR", "232": "SL", "233": "GH",
    "234": "NG", "235": "TD", "236": "CF",
    "237": "CM", "238": "CV", "239": "ST",
    "240": "GQ", "241": "GA", "242": "CG",
    "243": "CD", "244": "AO", "245": "GW",
    "248": "SC", "249": "SD", "250": "RW", "251": "ET",
    "252": "SO", "253": "DJ", "254": "KE", "255": "TZ",
    "256": "UG", "257": "BI", "258": "MZ", "260": "ZM",
    "261": "MG", "262": "RE", "263": "ZW", "264": "NA",
    "265": "MW", "266": "LS", "267": "BW", "268": "SZ",
    "269": "KM", "291": "ER", "297": "AW", "298": "FO",
    "299": "GL", "350": "GI", "351": "PT",
    "352": "LU", "353": "IE", "354": "IS", "355": "AL",
    "356": "MT", "357": "CY", "358": "FI", "359": "BG",
    "370": "LT", "371": "LV", "372": "EE", "373": "MD",
    "374": "AM", "375": "BY", "376": "AD", "377": "MC",
    "378": "SM", "380": "UA", "381": "RS", "382": "ME",
    "383": "XK", "385": "HR", "386": "SI",
    "387": "BA", "389": "MK",
    "420": "CZ", "421": "SK", "423": "LI",
    "500": "FK", "501": "BZ", "502": "GT",
    "503": "SV", "504": "HN", "505": "NI",
    "506": "CR", "507": "PA", "509": "HT", "591": "BO",
    "592": "GY", "593": "EC", "594": "GF",
    "595": "PY", "596": "MQ", "597": "SR", "598": "UY",
    "599": "CW", "670": "TL", "673": "BN", "674": "NR",
    "675": "PG", "676": "TO", "677": "SB",
    "678": "VU", "679": "FJ", "680": "PW", "682": "CK",
    "685": "WS", "686": "KI", "687": "NC", "688": "TV",
    "689": "PF", "691": "FM", "692": "MH",
    "850": "KP", "852": "HK", "853": "MO", "855": "KH",
    "856": "LA", "880": "BD", "886": "TW", "960": "MV",
    "961": "LB", "962": "JO", "963": "SY", "964": "IQ",
    "965": "KW", "966": "SA", "967": "YE", "968": "OM",
    "970": "PS", "971": "AE", "972": "IL",
    "973": "BH", "974": "QA", "975": "BT", "976": "MN",
    "977": "NP", "992": "TJ", "993": "TM",
    "994": "AZ", "995": "GE", "996": "KG",
    "998": "UZ",
}


def country_code_to_flag(iso: str) -> str:
    """Convert ISO 2-letter country code to flag emoji using Unicode
    regional indicator symbols (e.g. 'MM' → '🇲🇲')."""
    if not iso or len(iso) != 2:
        return "🌐"
    return ''.join(chr(0x1F1E6 + ord(c) - ord('A')) for c in iso.upper())


def _detect_iso_from_number(number: str) -> str:
    """Return the ISO 2-letter country code guessed from a phone number's
    leading dial-code. Tries 3-digit, then 2-digit, then 1-digit prefix."""
    if not number:
        return ''
    digits = re.sub(r'\D', '', str(number))
    if not digits:
        return ''
    for prefix_len in (3, 2, 1):
        if len(digits) >= prefix_len:
            iso = _DIAL_CODE_TO_ISO.get(digits[:prefix_len])
            if iso:
                return iso
    return ''


def _detect_sms_language(text: str) -> str:
    """Detect the natural language of an SMS body using Unicode script ranges."""
    if not text:
        return 'English'
    if re.search(r'[\u1000-\u109F]', text):
        return 'Burmese'
    if re.search(r'[\u0600-\u06FF]', text):
        return 'Arabic'
    if re.search(r'[\u0980-\u09FF]', text):
        return 'Bengali'
    if re.search(r'[\u0900-\u097F]', text):
        return 'Hindi'
    if re.search(r'[\u0400-\u04FF]', text):
        return 'Russian'
    if re.search(r'[\uAC00-\uD7AF]', text):
        return 'Korean'
    if re.search(r'[\u3040-\u30FF]', text):
        return 'Japanese'
    if re.search(r'[\u4E00-\u9FFF]', text):
        return 'Chinese'
    if re.search(r'[\u0E00-\u0E7F]', text):
        return 'Thai'
    if re.search(r'[\u0370-\u03FF]', text):
        return 'Greek'
    if re.search(r'[\u0590-\u05FF]', text):
        return 'Hebrew'
    return 'English'


# ── Service name → short code mapping ─────────────────────────────────────────
_SERVICE_SHORT_MAP: dict[str, str] = {
    'Facebook':   'FB',
    'Instagram':  'IG',
    'WhatsApp':   'WA',
    'Telegram':   'TG',
    'Google':     'GG',
    'Twitter/X':  'TW',
    'TikTok':     'TK',
    'Snapchat':   'SC',
    'Amazon':     'AM',
    'Netflix':    'NF',
    'Microsoft':  'MS',
    'Apple':      'AP',
    'PayPal':     'PP',
    'Uber':       'UB',
    'LinkedIn':   'LI',
    'Binance':    'BN',
    'Coinbase':   'CB',
    'Discord':    'DC',
    'Spotify':    'SP',
    'Shopify':    'SH',
    'Alibaba':    'AL',
    'Lazada':     'LZ',
    'Grab':       'GR',
    'Airbnb':     'AB',
    'eBay':       'EB',
}


def _get_service_short(website: str) -> str:
    """Return the 2-4 letter short code for a detected service/website name."""
    if not website or website in ('—', '-', 'Unknown', ''):
        return 'OTP'
    short = _SERVICE_SHORT_MAP.get(website)
    if short:
        return short
    clean = re.sub(r'[^A-Za-z]', '', website)
    return clean[:4].upper() if clean else 'OTP'


def _detect_country_from_number(number: str) -> str:
    """Return the country name guessed from a phone number's leading
    dial-code. Tries the 3-digit, then 2-digit, then 1-digit prefix.
    Returns '' when no match is found.
    """
    if not number:
        return ''
    digits = re.sub(r'\D', '', str(number))
    if not digits:
        return ''
    for prefix_len in (3, 2, 1):
        if len(digits) >= prefix_len:
            name = _COUNTRY_DIAL_CODES.get(digits[:prefix_len])
            if name:
                return name
    return ''


def _extract_country(range_name: str) -> str:
    """Extract only the country name from a range_name.
    Handles space-separated ('Syria Mtn Grand') and hyphen-separated
    ('Madagascar-Sacel-Cn-01') formats.
    """
    if not range_name:
        return ''
    cleaned = range_name.strip()

    # Handle fully hyphenated range names (no spaces): "Madagascar-Sacel-Cn-01"
    # Split by hyphen and use only the first segment as the candidate country.
    if '-' in cleaned and ' ' not in cleaned:
        cleaned = cleaned.split('-')[0].strip()
        if cleaned:
            return cleaned.title()
        return ''

    # Try to match known multi-word countries first (greedy, longest first)
    upper = cleaned.title()
    for country in _MULTI_WORD_COUNTRIES:
        if upper.startswith(country):
            return country

    # Otherwise take words one by one, stopping at operator/network keywords
    parts = cleaned.split()
    country_parts = []
    for part in parts:
        clean = part.strip('.,;:-()')
        if not clean:
            continue
        # Stop if digit found (e.g. "Zone1")
        if re.search(r'\d', clean):
            break
        lower = clean.lower()
        # Stop at short noise words
        if len(clean) <= 1 or lower in _SKIP_WORDS:
            break
        # Stop at telecom operator / suffix words
        if lower in _OPERATOR_STOP_WORDS:
            break
        country_parts.append(clean)
        # Allow at most 2 words for country name unless it's a known multi-word
        if len(country_parts) >= 2:
            break

    return ' '.join(country_parts).title() if country_parts else cleaned.split()[0].title()


def _extract_otp(sms_body: str) -> str:
    if not sms_body:
        return ''
    for pat in _OTP_PATTERNS:
        m = pat.search(sms_body)
        if m:
            return m.group(1)
    return ''


def _extract_all_otps(sms_body: str) -> str:
    """Extract ALL OTP-like numbers (4-8 digits) from the SMS body, deduplicated."""
    if not sms_body:
        return '—'
    matches = re.findall(r'\b(\d{4,8})\b', sms_body)
    if not matches:
        return '—'
    seen: set[str] = set()
    unique: list[str] = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            unique.append(m)
    return ' | '.join(unique)


def _build_sms_notify_text(number: str, website: str, sms_body: str,
                            old_balance=None, new_balance=None,
                            bonus_amount=None) -> str:
    """Build unified HTML notification text for all panels."""
    otp       = _extract_otp(sms_body) or '—'
    safe_site = _html.escape(str(website or '—'))
    safe_num  = _html.escape(str(number or ''))
    safe_otp  = _html.escape(str(otp))
    safe_body = _html.escape(str(sms_body or '—'))
    parts = [
        f"📱 {safe_site}",
        "",
        f"📞GET NUMBER: +{safe_num}",
        "",
        f"🔒 OTP : <code>{safe_otp}</code>",
        "",
        "💬 Full Message :",
        f"<code>{safe_body}</code>",
    ]
    if bonus_amount is not None and new_balance is not None:
        parts += [
            "",
            f"💰 +{bonus_amount:.2f}৳ »»»–»»» {new_balance:.2f}৳ 💸",
        ]
    parts += [
        "",
        "😅 Thanks For using @UnofficialNumberBOT",
    ]
    return "\n".join(parts)


# ── Cross-panel group-broadcast deduplication ─────────────────────────────────
# Prevents the same SMS from being forwarded to the group more than once when
# multiple panels (e.g. Konekta + MSI SMS) see the same record simultaneously.
_GROUP_BROADCAST_SEEN: set[str] = set()


def _group_broadcast_key(number: str, sms_body: str) -> str:
    """Stable cross-panel key for a single SMS event.

    Intentionally excludes panel_name and dt_str so that the same SMS
    detected by multiple panels (even with slightly different timestamps)
    is treated as one event and sent to the group only once.
    """
    return hashlib.sha256(
        f"grp:{number}:{sms_body}".encode()
    ).hexdigest()


async def _broadcast_to_groups(
    bot, panel_name: str, grp_text: str, grp_markup,
    dt_str: str = '', number: str = '', sms_body: str = '',
):
    """Send the OTP notification to the main group AND every extra group
    independently — failure of one does not block the others. Extra-group
    sends run concurrently for speed (important under 15k user load).

    Cross-panel deduplication: uses a module-level in-memory set PLUS the
    persistent DB so that only the FIRST panel to reach this call actually
    broadcasts — even across bot restarts."""

    # ── Cross-panel duplicate guard ───────────────────────────────────────────
    if number and sms_body:
        from database import _is_otp_delivered, _mark_otp_delivered
        gkey = _group_broadcast_key(number, sms_body)
        # Fast in-memory check first
        if gkey in _GROUP_BROADCAST_SEEN:
            logger.info(
                f"{panel_name}: group broadcast skipped — "
                "already sent by another panel (in-memory)."
            )
            return
        # Persistent DB check (survives restarts)
        if await _otp_thread(_is_otp_delivered, gkey):
            _GROUP_BROADCAST_SEEN.add(gkey)
            logger.info(
                f"{panel_name}: group broadcast skipped — "
                "already sent by another panel (DB check)."
            )
            return
        # Claim the key — mark BEFORE sending to prevent races
        _GROUP_BROADCAST_SEEN.add(gkey)
        await _otp_thread(_mark_otp_delivered, gkey)

    # Main group
    try:
        await bot.send_message(chat_id=GROUP_CHAT_ID, text=grp_text,
                               parse_mode='HTML', reply_markup=grp_markup)
    except Exception as _ge:
        logger.warning(f"{panel_name}: main group notify failed — {_ge}")

    # Extra groups (always attempted, regardless of main group result)
    try:
        from database import _get_all_extra_groups
        extra_groups = await _otp_thread(_get_all_extra_groups)
    except Exception as _ege2:
        logger.warning(f"{panel_name}: extra groups fetch failed — {_ege2}")
        return

    if not extra_groups:
        return

    async def _send_one(eg):
        try:
            await bot.send_message(chat_id=eg['chat_id'], text=grp_text,
                                   parse_mode='HTML', reply_markup=grp_markup)
        except Exception as _ege:
            logger.warning(f"{panel_name}: extra group {eg['chat_id']} send failed — {_ege}")

    await asyncio.gather(*(_send_one(eg) for eg in extra_groups),
                         return_exceptions=True)


def _build_group_notify_text(number: str, country: str, website: str, otp: str, sms_body: str) -> tuple:
    """Build the unified group-broadcast message used by all 10 panels.

    Format:
        {flag} #{country_short} #{service_short}✅{masked_number} {sms_language}

        👨‍💻BOT RUN BY : <a href="https://t.me/limonff143">L I M O N</a>

    Returns (html_text, InlineKeyboardMarkup).
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    # ── ISO code + flag ──────────────────────────────────────────────────────
    iso          = _detect_iso_from_number(number) or 'XX'
    flag         = country_code_to_flag(iso)
    country_short = iso

    # ── Service short code ───────────────────────────────────────────────────
    final_website = (website or '').strip()
    if not final_website or final_website.lower() in ('unknown', '—', '-'):
        detected = _detect_website_from_body(sms_body)
        if detected and detected != 'Unknown':
            final_website = detected
        else:
            final_website = ''
    service_short = _get_service_short(final_website)

    # ── Number masking: first 3 digits + ★★★ + last 4 digits ───────────────
    digits_only = re.sub(r'\D', '', str(number or ''))
    if len(digits_only) > 7:
        masked = digits_only[:3] + '★' * (len(digits_only) - 7) + digits_only[-4:]
    elif digits_only:
        masked = digits_only
    else:
        masked = '—'

    # ── SMS language detection ───────────────────────────────────────────────
    sms_lang = _detect_sms_language(sms_body or '')

    # ── OTP value ────────────────────────────────────────────────────────────
    otp_clean = (otp or '—').strip()

    text = f"{flag}#{country_short} #{service_short} {masked} • {sms_lang}"

    from database import _get_setting
    lnk_number  = _get_setting("otp_btn_number",  "https://t.me/UnofficialNumberBOT?start=start")
    lnk_channel = _get_setting("otp_btn_channel", "https://t.me/UnofficialNumber")

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(otp_clean, callback_data=f"copy_otp:{otp_clean}")],
        [
            InlineKeyboardButton("NUMBER",  url=lnk_number),
            InlineKeyboardButton("CHANNEL", url=lnk_channel),
        ],
    ])
    return text, markup


def _detect_website_from_body(message: str) -> str:
    if not message:
        return 'Unknown'
    for pattern, name in _WEBSITE_PATTERNS:
        if pattern.search(message):
            return name
    url_match = re.search(
        r'(?:https?://)?(?:www\.)?([a-zA-Z0-9\-]+)\.[a-z]{2,}', message
    )
    if url_match:
        domain = url_match.group(1).capitalize()
        if len(domain) > 2:
            return domain
    return 'Unknown'


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({'User-Agent': _USER_AGENT})
    return s


# ── SMS Hadi AJAX URL builder ─────────────────────────────────────────────────

def _build_hadi_ajax_url(sesskey: str, days_back: int = 7) -> str:
    now = datetime.now()
    d1  = (now - timedelta(days=days_back)).strftime('%Y-%m-%d 00:00:00')
    d2  = now.strftime('%Y-%m-%d 23:59:59')
    params = urlencode({
        'fdate1': d1, 'fdate2': d2,
        'frange': '', 'fclient': '', 'fnum': '', 'fcli': '',
        'fgdate': '', 'fgmonth': '', 'fgrange': '', 'fgclient': '',
        'fgnumber': '', 'fgcli': '',
        'fg': '0',
        'sesskey': sesskey,
        'iDisplayStart': '0',
        'iDisplayLength': '999999',
        'iSortCol_0': '0',
        'sSortDir_0': 'desc',
    })
    return f"{SMS_HADI_AJAX_BASE}?{params}"


# ── Client panel AJAX URL builder (Konekta / MSI SMS) ────────────────────────

def _build_client_ajax_url(ajax_base: str, days_back: int = 7) -> str:
    now = datetime.now()
    d1  = (now - timedelta(days=days_back)).strftime('%Y-%m-%d 00:00:00')
    d2  = now.strftime('%Y-%m-%d 23:59:59')
    params = urlencode({
        'fdate1': d1, 'fdate2': d2,
        'frange': '', 'fnum': '', 'fcli': '',
        'fgdate': '', 'fgmonth': '', 'fgrange': '', 'fgnumber': '', 'fgcli': '',
        'fg': '0',
        'iDisplayStart': '0',
        'iDisplayLength': '999999',
        'iSortCol_0': '0',
        'sSortDir_0': 'desc',
    })
    return f"{ajax_base}?{params}"


# ══════════════════════════════════════════════════════════════════════════════
# SMS Hadi Monitor  (sesskey-based)
# ══════════════════════════════════════════════════════════════════════════════

class OTPMonitor:
    """Background monitor for SMS Hadi panel (sesskey-based auth)."""

    def __init__(self):
        self.panel_name     = 'SMS Hadi'
        self.interval       = SMS_MONITOR_INTERVAL
        self.retry_interval = 60
        self._running       = False
        self._task          = None
        self._seen_keys: set[str] = set()
        self._is_first_poll = True
        self.session: requests.Session | None = None
        self.logged_in      = False
        self._sesskey       = None
        self._manual_only   = False  # If True, _loop will not auto-login (set after Session Cleanup)
        self._username      = SMS_HADI_USERNAME
        self._password      = SMS_HADI_PASSWORD
        self._latest_record = None   # cached latest SMS for get_latest_today()

    def set_interval(self, seconds: int):
        """Update the polling interval live (no restart needed)."""
        self.interval = max(1, int(seconds))

    def set_retry_interval(self, seconds: int):
        """Update the login-retry interval live (no restart needed)."""
        self.retry_interval = max(1, int(seconds))

    def _refresh_credentials(self):
        """Read latest credentials from DB so admin-panel changes take effect."""
        try:
            from database import _get_panel_by_name
            p = _get_panel_by_name(self.panel_name)
            if p and p.get('username'):
                self._username = p['username']
                self._password = p['password']
        except Exception:
            pass

    def _login(self) -> bool:
        self._refresh_credentials()
        if not self._username or not self._password:
            logger.warning("OTPMonitor: credentials not set.")
            return False
        last_reason = "unknown"
        for attempt in range(1, _LOGIN_FAST_RETRIES + 1):
            try:
                # Fresh Session Reload — drop any old cookies and rebuild
                self.session = _new_session()
                r1 = self.session.get(SMS_HADI_LOGIN_URL, timeout=15)
                captcha = _solve_captcha(r1.text)
                if captcha is None:
                    last_reason = "captcha unsolvable"
                    logger.warning(
                        f"OTPMonitor: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                logger.info(
                    f"OTPMonitor: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — Captcha solved → {captcha}"
                )
                r2 = self.session.post(
                    SMS_HADI_SIGNIN_URL,
                    data={'username': self._username, 'password': self._password, 'capt': captcha},
                    headers={'Referer': SMS_HADI_LOGIN_URL},
                    timeout=15, allow_redirects=True,
                )
                if 'login' in r2.url.lower():
                    last_reason = "login rejected"
                    logger.warning(
                        f"OTPMonitor: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self.logged_in = True
                logger.info(
                    f"OTPMonitor: Logged in successfully (attempt {attempt}/{_LOGIN_FAST_RETRIES})."
                )
                return True
            except Exception as exc:
                last_reason = f"exception: {exc}"
                logger.warning(
                    f"OTPMonitor: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}"
                )
                if attempt < _LOGIN_FAST_RETRIES:
                    time.sleep(_LOGIN_RETRY_DELAY)
        logger.error(
            f"OTPMonitor: All {_LOGIN_FAST_RETRIES} login attempts failed — {last_reason}"
        )
        self.logged_in = False
        return False

    def _extract_sesskey(self) -> bool:
        try:
            now = datetime.now()
            r = self.session.post(
                SMS_HADI_REPORTS_URL,
                data={
                    'fdate1': (now - timedelta(days=1)).strftime('%Y-%m-%d 00:00:00'),
                    'fdate2': now.strftime('%Y-%m-%d 23:59:59'),
                    'fnum': '', 'fcli': '', 'frange': '', 'fclient': '',
                },
                headers={'Referer': SMS_HADI_REPORTS_URL},
                timeout=20,
            )
            if 'login' in r.url.lower():
                self.logged_in = False
                return False
            m = re.search(
                r'"sAjaxSource"\s*:\s*"res/data_smscdr\.php[^"]*sesskey=([^"&]+)"',
                r.text
            )
            if not m:
                logger.error("OTPMonitor: sesskey not found.")
                return False
            self._sesskey = m.group(1)
            logger.info("OTPMonitor: sesskey extracted successfully.")
            return True
        except Exception as exc:
            logger.error(f"OTPMonitor: _extract_sesskey error — {exc}")
            return False

    def _fetch_individual_records(self) -> list[dict] | None:
        if not self._sesskey:
            return None
        try:
            url = _build_hadi_ajax_url(self._sesskey)
            r = self.session.get(
                url,
                headers={'Referer': SMS_HADI_REPORTS_URL, 'X-Requested-With': 'XMLHttpRequest'},
                timeout=25,
            )
            if 'login' in r.url.lower():
                self.logged_in = False
                return None
            data = r.json()
            rows = data.get('aaData', [])
            results = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 6:
                    continue
                dt_str     = str(row[0]).strip() if row[0] else ''
                range_name = str(row[1]).strip() if row[1] else ''
                number     = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
                website    = str(row[3]).strip() if row[3] else 'Unknown'
                sms_body   = str(row[5]).strip() if len(row) > 5 and row[5] else ''
                detected = _detect_website_from_body(sms_body)
                if detected and detected != 'Unknown':
                    website = detected
                if not number or not website or not dt_str:
                    continue
                results.append({
                    'datetime': dt_str, 'range_name': range_name,
                    'number': number, 'website': website, 'sms_body': sms_body,
                })
            logger.info(f"OTPMonitor: Fetched {len(results)} SMS records.")
            return results
        except Exception as exc:
            logger.error(f"OTPMonitor: _fetch_individual_records error — {exc}")
            return None

    def fetch_24h_count(self, website_name: str) -> int:
        if not self._sesskey or not self.logged_in:
            return -1
        try:
            now = datetime.now()
            d1  = (now - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
            d2  = now.strftime('%Y-%m-%d %H:%M:%S')
            url = (
                f"{SMS_HADI_BASE}/agent/res/data_smscdr.php?"
                + urlencode({
                    'fdate1': d1, 'fdate2': d2,
                    'frange': '', 'fclient': '', 'fnum': '', 'fcli': '',
                    'fgdate': '1', 'fgmonth': '1', 'fgrange': '1',
                    'fgclient': '1', 'fgnumber': '1', 'fgcli': '1',
                    'fg': '1', 'sesskey': self._sesskey,
                    'iDisplayStart': '0', 'iDisplayLength': '999999',
                })
            )
            r = self.session.get(
                url,
                headers={'Referer': f"{SMS_HADI_BASE}/agent/SMSCDRStats",
                         'X-Requested-With': 'XMLHttpRequest'},
                timeout=20,
            )
            if 'login' in r.url.lower():
                self.logged_in = False
                return -1
            data  = r.json()
            rows  = data.get('aaData', [])
            total = 0
            for row in rows:
                if not isinstance(row, list) or len(row) < 7:
                    continue
                client = str(row[5]).strip() if row[5] else ''
                if client.lower() == website_name.lower():
                    try:
                        total += int(str(row[6]).strip())
                    except (ValueError, TypeError):
                        pass
            return total
        except Exception as exc:
            logger.error(f"OTPMonitor: fetch_24h_count error — {exc}")
            return -1

    def get_latest_today(self) -> 'dict | None':
        """Return cached latest SMS; fall back to live HTTP fetch if cache is empty."""
        if self._latest_record:
            return self._latest_record
        if not self.logged_in or not self.session or not self._sesskey:
            return None
        try:
            now = datetime.now()
            params = urlencode({
                'fdate1': (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00'),
                'fdate2': now.strftime('%Y-%m-%d 23:59:59'),
                'frange': '', 'fclient': '', 'fnum': '', 'fcli': '',
                'fgdate': '', 'fgmonth': '', 'fgrange': '', 'fgclient': '',
                'fgnumber': '', 'fgcli': '', 'fg': '0',
                'sesskey': self._sesskey,
                'iDisplayStart': '0', 'iDisplayLength': '999999',
                'iSortCol_0': '0', 'sSortDir_0': 'desc',
            })
            r = self.session.get(
                f"{SMS_HADI_AJAX_BASE}?{params}",
                headers={'Referer': SMS_HADI_REPORTS_URL, 'X-Requested-With': 'XMLHttpRequest'},
                timeout=20,
            )
            if 'login' in r.url.lower():
                self.logged_in = False
                return None
            rows = r.json().get('aaData', [])
            valid = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 6:
                    continue
                dt_str = str(row[0]).strip() if row[0] else ''
                if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                    continue
                number = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
                if not number:
                    continue
                sms_body = str(row[5]).strip() if len(row) > 5 and row[5] else ''
                valid.append({
                    'dt': dt_str, 'number': number,
                    'website': str(row[3]).strip() if row[3] else '',
                    'range_name': str(row[1]).strip() if row[1] else '',
                    'sms_body': sms_body,
                })
            if not valid:
                return None
            valid.sort(key=lambda x: x['dt'], reverse=True)
            rec = valid[0]
            uid = hashlib.md5(f"{rec['dt']}:{rec['number']}:{rec['sms_body']}".encode()).hexdigest()
            result = {
                'id': uid, 'datetime': rec['dt'], 'number': rec['number'],
                'website': rec['website'] or _detect_website_from_body(rec['sms_body']),
                'country': _extract_country(rec['range_name']),
                'otp': _extract_otp(rec['sms_body']), 'message': rec['sms_body'],
                'received_at': rec['dt'], 'panel_name': self.panel_name,
            }
            self._latest_record = result
            return result
        except Exception as exc:
            logger.error(f"OTPMonitor: get_latest_today error — {exc}")
            return None

    async def _notify_user(self, bot, number: str, website: str, otp: str, sms_body: str, delivery_key: str = ''):
        try:
            from database import (
                _get_recent_user_by_number as _gub,
                _get_otp_bonus_settings,
                _has_otp_bonus_received, _record_otp_bonus,
                _get_effective_otp_bonus, _get_user_balance,
            )
            uid = await _otp_thread(_gub, number)
            if not uid:
                uid = await _otp_thread(_gub, '+' + number)
            if uid and bot:
                bonus_amount_credited = None
                new_balance = None
                if delivery_key:
                    bonus_cfg = await _otp_thread(_get_otp_bonus_settings)
                    if bonus_cfg['enabled']:
                        already = await _otp_thread(_has_otp_bonus_received, delivery_key)
                        if not already:
                            effective_amount = await _otp_thread(_get_effective_otp_bonus, number, bonus_cfg['amount'])
                            credited = await _otp_thread(
                                _record_otp_bonus, uid, delivery_key, effective_amount
                            )
                            if credited:
                                new_balance = await _otp_thread(_get_user_balance, uid)
                                bonus_amount_credited = effective_amount
                                logger.info(f"OTPMonitor: OTP bonus BDT {effective_amount:.2f} credited to user {uid}")
                notify_text = _build_sms_notify_text(
                    number, website, sms_body,
                    bonus_amount=bonus_amount_credited,
                    new_balance=new_balance,
                )
                await bot.send_message(chat_id=uid, text=notify_text, parse_mode='HTML')
                logger.info(f"OTPMonitor: Notified user {uid} about SMS for +{number}")
        except Exception as notify_exc:
            logger.warning(f"OTPMonitor: Could not notify user — {notify_exc}")

    async def _loop(self, bot):
        from database import (_is_otp_delivered, _mark_otp_delivered,
                              _update_panel_status, _is_panel_enabled)
        pname = self.panel_name
        logger.info("OTPMonitor: Starting.")
        # ── Wait until panel is enabled before attempting login
        while self._running and (
            not _is_panel_enabled(pname)
            or getattr(self, '_manual_only', False)
        ):
            await asyncio.sleep(5)
        if not self._running:
            return
        ok = await _otp_thread(self._login)
        _login_fail_notified = False
        while not ok and self._running:
            logger.warning(f"OTPMonitor: Login failed — retrying in {self.retry_interval}s…")
            await _otp_thread(_update_panel_status, pname, False, None, 'Login failed — retrying')
            if not _login_fail_notified:
                await _notify_admins_login_fail(bot, pname)
                _login_fail_notified = True
            await asyncio.sleep(self.retry_interval)
            while self._running and (
                not _is_panel_enabled(pname)
                or getattr(self, '_manual_only', False)
            ):
                await asyncio.sleep(5)
            if not self._running:
                return
            ok = await _otp_thread(self._login)
        if not self._running:
            return
        await _otp_thread(_update_panel_status, pname, True)
        if _login_fail_notified:
            await _notify_admins_login_success(bot, pname)
            _login_fail_notified = False
        ok = await _otp_thread(self._extract_sesskey)
        if not ok:
            logger.error("OTPMonitor: Could not extract sesskey.")
            return

        while self._running:
            if not _is_panel_enabled(pname) or getattr(self, '_manual_only', False):
                await asyncio.sleep(5)
                continue
            try:
                records = await _otp_thread(self._fetch_individual_records)

                if records is None:
                    logger.info("OTPMonitor: Session expired — re-logging in …")
                    # Wait if in manual-only mode (set after Session Cleanup)
                    while self._running and getattr(self, '_manual_only', False):
                        await asyncio.sleep(5)
                    if not self._running:
                        return
                    ok = await _otp_thread(self._login)
                    if ok:
                        await _otp_thread(_update_panel_status, pname, True)
                        await _otp_thread(self._extract_sesskey)
                    await asyncio.sleep(self.interval)
                    continue
                await _otp_thread(_update_panel_status, pname, True, len(records))

                # Always update _latest_record with the most recent SMS from each poll
                if records:
                    _r0 = records[0]
                    uid0 = hashlib.md5(f"{_r0['datetime']}:{_r0['number']}:{_r0['sms_body']}".encode()).hexdigest()
                    self._latest_record = {
                        'id': uid0, 'datetime': _r0['datetime'], 'number': _r0['number'],
                        'website': _r0['website'] or _detect_website_from_body(_r0['sms_body']),
                        'country': _extract_country(_r0['range_name']),
                        'otp': _extract_otp(_r0['sms_body']), 'message': _r0['sms_body'],
                        'received_at': _r0['datetime'], 'panel_name': pname,
                    }

                for rec in records:
                    dt_str     = rec['datetime']
                    range_name = rec['range_name']
                    number     = rec['number']
                    website    = rec['website']
                    sms_body   = rec['sms_body']

                    delivery_key = hashlib.sha256(
                        f"{dt_str}:{number}:{website}:{sms_body}".encode()
                    ).hexdigest()

                    if delivery_key in self._seen_keys:
                        continue
                    already = await _otp_thread(_is_otp_delivered, delivery_key)
                    if already:
                        self._seen_keys.add(delivery_key)
                        continue

                    country = _extract_country(range_name)
                    otp     = _extract_otp(sms_body)

                    await _otp_thread(_mark_otp_delivered, delivery_key)
                    self._seen_keys.add(delivery_key)

                    if not self._is_first_poll:
                        logger.info(
                            f"OTPMonitor: NEW SMS — website={website}, "
                            f"number=+{number}, otp={otp or '—'}"
                        )
                        await self._notify_user(bot, number, website, otp, sms_body, delivery_key)
                        try:
                            grp_text, grp_markup = _build_group_notify_text(number, country, website, otp, sms_body)
                            await _broadcast_to_groups(bot, pname, grp_text, grp_markup,
                                                       dt_str=dt_str, number=number, sms_body=sms_body)
                        except Exception as _ge:
                            logger.warning(f"OTPMonitor: group notify failed — {_ge}")

            except Exception as exc:
                logger.error(f"OTPMonitor: Unexpected error — {exc}")

            self._is_first_poll = False
            await asyncio.sleep(self.interval)

    def start(self, bot):
        self._running = True
        self._task    = asyncio.create_task(self._loop(bot))
        logger.info("OTPMonitor: Task created.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("OTPMonitor: Stopped.")


# ══════════════════════════════════════════════════════════════════════════════
# OTPMonitor2 — Second SMS Hadi account (same server, different credentials)
# ══════════════════════════════════════════════════════════════════════════════

class OTPMonitor2(OTPMonitor):
    """Second SMS Hadi account monitor — same smshadi.net server, different
    credentials. Inherits all session/sesskey/fetch logic from OTPMonitor;
    only _login is overridden to use SMS_HADI2_* credentials."""

    def __init__(self):
        super().__init__()
        self.panel_name = 'SMS Hadi 2'
        self._username  = SMS_HADI2_USERNAME
        self._password  = SMS_HADI2_PASSWORD

    def _refresh_credentials(self):
        """Read latest credentials from DB so admin-panel changes take effect."""
        try:
            from database import _get_panel_by_name
            p = _get_panel_by_name('SMS Hadi 2')
            if p and p.get('username'):
                self._username = p['username']
                self._password = p['password']
        except Exception:
            pass

    def _login(self) -> bool:
        self._refresh_credentials()
        if not self._username or not self._password:
            logger.warning("OTPMonitor2: credentials not set — set via Admin Panel → Panel List → SMS Hadi 2 → Edit Credentials")
            return False
        last_reason = "unknown"
        for attempt in range(1, _LOGIN_FAST_RETRIES + 1):
            try:
                self.session = _new_session()
                r1 = self.session.get(SMS_HADI2_LOGIN_URL, timeout=15)
                captcha = _solve_captcha(r1.text)
                if captcha is None:
                    last_reason = "captcha unsolvable"
                    logger.warning(f"OTPMonitor2: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}.")
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                logger.info(f"OTPMonitor2: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — Captcha solved → {captcha}")
                r2 = self.session.post(
                    SMS_HADI2_SIGNIN_URL,
                    data={'username': self._username, 'password': self._password, 'capt': captcha},
                    headers={'Referer': SMS_HADI2_LOGIN_URL},
                    timeout=15, allow_redirects=True,
                )
                if 'login' in r2.url.lower():
                    last_reason = "login rejected"
                    logger.warning(f"OTPMonitor2: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}.")
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self.logged_in = True
                logger.info(f"OTPMonitor2: Logged in successfully (attempt {attempt}/{_LOGIN_FAST_RETRIES}).")
                return True
            except Exception as exc:
                last_reason = f"exception: {exc}"
                logger.warning(f"OTPMonitor2: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}")
                if attempt < _LOGIN_FAST_RETRIES:
                    time.sleep(_LOGIN_RETRY_DELAY)
        logger.error(f"OTPMonitor2: All {_LOGIN_FAST_RETRIES} login attempts failed — {last_reason}")
        self.logged_in = False
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Generic Client Panel Monitor  (cookie-based, /client/ path)
# Used for: Konekta Premium, MSI SMS (and any future similar panel)
# ══════════════════════════════════════════════════════════════════════════════

class ClientPanelMonitor:
    """
    Generic background monitor for panels that use:
      - Cookie-based session (no sesskey)
      - /client/SMSCDRStats stats page
      - /client/res/data_smscdr.php AJAX endpoint
      - Columns: [0]datetime [1]range [2]number [3]cli [4]sms_body

    Website name is detected automatically from SMS body text.
    """

    def __init__(
        self,
        panel_name: str,
        base_url: str,
        login_page_url: str,
        signin_url: str,
        username: str,
        password: str,
        path_prefix: str = "client",
    ):
        self.panel_name    = panel_name
        self.base_url      = base_url.rstrip('/')
        self.login_page    = login_page_url
        self.signin_url    = signin_url
        self.username      = username
        self.password      = password
        self.ajax_url      = f"{self.base_url}/{path_prefix}/res/data_smscdr.php"
        self.referer_url   = f"{self.base_url}/{path_prefix}/SMSCDRStats"
        self._log          = logging.getLogger(f"otp_monitor.{panel_name}")

        self.interval       = SMS_MONITOR_INTERVAL
        self.retry_interval = 60
        self._running       = False
        self._task          = None
        self._seen_keys: set[str] = set()
        self._is_first_poll = True
        self.session: requests.Session | None = None
        self.logged_in      = False
        self._manual_only   = False
        self._latest_record = None   # cached latest SMS for get_latest_today()

    def set_interval(self, seconds: int):
        """Update the polling interval live (no restart needed)."""
        self.interval = max(1, int(seconds))

    def set_retry_interval(self, seconds: int):
        """Update the login-retry interval live (no restart needed)."""
        self.retry_interval = max(1, int(seconds))

    def _refresh_credentials(self):
        """Read latest credentials from DB so admin-panel changes take effect."""
        try:
            from database import _get_panel_by_name
            p = _get_panel_by_name(self.panel_name)
            if p and p.get('username'):
                self.username = p['username']
                self.password = p['password']
        except Exception:
            pass

    def _login(self) -> bool:
        self._refresh_credentials()
        if not self.username or not self.password:
            self._log.warning(f"{self.panel_name}: credentials not set.")
            return False
        last_reason = "unknown"
        for attempt in range(1, _LOGIN_FAST_RETRIES + 1):
            try:
                # Fresh Session Reload — drop any old cookies and rebuild
                self.session = _new_session()
                r1 = self.session.get(self.login_page, timeout=15)
                captcha = _solve_captcha(r1.text)
                if captcha is None:
                    last_reason = "captcha unsolvable"
                    self._log.warning(
                        f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self._log.info(
                    f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — Captcha solved → {captcha}"
                )
                r2 = self.session.post(
                    self.signin_url,
                    data={'username': self.username, 'password': self.password, 'capt': captcha},
                    headers={'Referer': self.login_page},
                    timeout=15, allow_redirects=True,
                )
                # Detect failed login by checking if we landed back on a login page
                final_path = r2.url.lower()
                if 'login' in final_path or 'sign-in' in final_path or 'signin' in final_path.split('/')[-1]:
                    last_reason = "login rejected"
                    self._log.warning(
                        f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self.logged_in = True
                self._log.info(
                    f"{self.panel_name}: Logged in successfully (attempt {attempt}/{_LOGIN_FAST_RETRIES})."
                )
                return True
            except Exception as exc:
                last_reason = f"exception: {exc}"
                self._log.warning(
                    f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}"
                )
                if attempt < _LOGIN_FAST_RETRIES:
                    time.sleep(_LOGIN_RETRY_DELAY)
        self._log.error(
            f"{self.panel_name}: All {_LOGIN_FAST_RETRIES} login attempts failed — {last_reason}"
        )
        self.logged_in = False
        return False

    def _fetch_records(self) -> list[dict] | None:
        try:
            url = _build_client_ajax_url(self.ajax_url, days_back=7)
            r = self.session.get(
                url,
                headers={
                    'Referer': self.referer_url,
                    'X-Requested-With': 'XMLHttpRequest',
                },
                timeout=25,
            )
            final_path = r.url.lower()
            if 'login' in final_path or 'sign-in' in final_path:
                self.logged_in = False
                return None

            data = r.json()
            rows = data.get('aaData', [])

            results = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 5:
                    continue
                dt_str     = str(row[0]).strip() if row[0] else ''
                range_name = str(row[1]).strip() if row[1] else ''
                number     = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
                # Skip totals/summary rows — they don't have a valid datetime
                if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                    continue
                # row[3] = CLI (sender) — website is in SMS body, not here
                sms_body   = str(row[4]).strip() if len(row) > 4 and row[4] else ''

                if not number or not dt_str:
                    continue

                website = _detect_website_from_body(sms_body)

                results.append({
                    'datetime':   dt_str,
                    'range_name': range_name,
                    'number':     number,
                    'website':    website,
                    'sms_body':   sms_body,
                })

            self._log.info(f"{self.panel_name}: Fetched {len(results)} SMS records.")
            return results

        except Exception as exc:
            self._log.error(f"{self.panel_name}: _fetch_records error — {exc}")
            return None


    def get_latest_today(self) -> 'dict | None':
        """Return cached latest SMS; fall back to live HTTP fetch if cache is empty."""
        if self._latest_record:
            return self._latest_record
        if not self.logged_in or not self.session:
            return None
        try:
            now = datetime.now()
            params = urlencode({
                'fdate1': (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00'),
                'fdate2': now.strftime('%Y-%m-%d 23:59:59'),
                'frange': '', 'fnum': '', 'fcli': '',
                'fgdate': '', 'fgmonth': '', 'fgrange': '', 'fgnumber': '', 'fgcli': '',
                'fg': '0', 'iDisplayStart': '0', 'iDisplayLength': '999999',
                'iSortCol_0': '0', 'sSortDir_0': 'desc',
            })
            r = self.session.get(
                f"{self.ajax_url}?{params}",
                headers={'Referer': self.referer_url, 'X-Requested-With': 'XMLHttpRequest'},
                timeout=20,
            )
            if 'login' in r.url.lower() or 'sign-in' in r.url.lower():
                self.logged_in = False
                return None
            rows = r.json().get('aaData', [])
            valid = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 5:
                    continue
                dt_str = str(row[0]).strip() if row[0] else ''
                if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                    continue
                number = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
                if not number:
                    continue
                sms_body = str(row[4]).strip() if len(row) > 4 and row[4] else ''
                valid.append({
                    'dt': dt_str, 'number': number,
                    'range_name': str(row[1]).strip() if row[1] else '',
                    'sms_body': sms_body,
                })
            if not valid:
                return None
            valid.sort(key=lambda x: x['dt'], reverse=True)
            rec = valid[0]
            uid = hashlib.md5(f"{rec['dt']}:{rec['number']}:{rec['sms_body']}".encode()).hexdigest()
            result = {
                'id': uid, 'datetime': rec['dt'], 'number': rec['number'],
                'website': _detect_website_from_body(rec['sms_body']),
                'country': _extract_country(rec['range_name']),
                'otp': _extract_otp(rec['sms_body']), 'message': rec['sms_body'],
                'received_at': rec['dt'], 'panel_name': self.panel_name,
            }
            self._latest_record = result
            return result
        except Exception as exc:
            self._log.error(f"{self.panel_name}: get_latest_today error — {exc}")
            return None

    async def _notify_user(self, bot, number: str, website: str, otp: str, sms_body: str, delivery_key: str = ''):
        try:
            from database import (
                _get_recent_user_by_number as _gub,
                _get_otp_bonus_settings,
                _has_otp_bonus_received, _record_otp_bonus,
                _get_effective_otp_bonus, _get_user_balance,
            )
            uid = await _otp_thread(_gub, number)
            if not uid:
                uid = await _otp_thread(_gub, '+' + number)
            if uid and bot:
                bonus_amount_credited = None
                new_balance = None
                if delivery_key:
                    bonus_cfg = await _otp_thread(_get_otp_bonus_settings)
                    if bonus_cfg['enabled']:
                        already = await _otp_thread(_has_otp_bonus_received, delivery_key)
                        if not already:
                            effective_amount = await _otp_thread(_get_effective_otp_bonus, number, bonus_cfg['amount'])
                            credited = await _otp_thread(
                                _record_otp_bonus, uid, delivery_key, effective_amount
                            )
                            if credited:
                                new_balance = await _otp_thread(_get_user_balance, uid)
                                bonus_amount_credited = effective_amount
                                self._log.info(f"{self.panel_name}: OTP bonus BDT {effective_amount:.2f} credited to user {uid}")
                notify_text = _build_sms_notify_text(
                    number, website, sms_body,
                    bonus_amount=bonus_amount_credited,
                    new_balance=new_balance,
                )
                await bot.send_message(chat_id=uid, text=notify_text, parse_mode='HTML')
                self._log.info(f"{self.panel_name}: Notified user {uid} for +{number}")
        except Exception as notify_exc:
            self._log.warning(f"{self.panel_name}: Could not notify user — {notify_exc}")

    async def _loop(self, bot):
        from database import (_is_otp_delivered, _mark_otp_delivered,
                              _update_panel_status, _is_panel_enabled)
        self._log.info(f"{self.panel_name}: Starting.")

        # ── Wait until panel is enabled before attempting login
        while self._running and (
            not _is_panel_enabled(self.panel_name)
            or getattr(self, '_manual_only', False)
        ):
            await asyncio.sleep(5)
        if not self._running:
            return

        ok = await _otp_thread(self._login)
        _login_fail_notified = False
        while not ok and self._running:
            self._log.warning(f"{self.panel_name}: Login failed — retrying in {self.retry_interval}s…")
            await _otp_thread(_update_panel_status, self.panel_name, False, None, 'Login failed — retrying')
            if not _login_fail_notified:
                await _notify_admins_login_fail(bot, self.panel_name)
                _login_fail_notified = True
            await asyncio.sleep(self.retry_interval)
            while self._running and (
                not _is_panel_enabled(self.panel_name)
                or getattr(self, '_manual_only', False)
            ):
                await asyncio.sleep(5)
            if not self._running:
                return
            ok = await _otp_thread(self._login)
        if not self._running:
            return
        await _otp_thread(_update_panel_status, self.panel_name, True)
        if _login_fail_notified:
            await _notify_admins_login_success(bot, self.panel_name)
            _login_fail_notified = False

        while self._running:
            if not _is_panel_enabled(self.panel_name) or getattr(self, '_manual_only', False):
                await asyncio.sleep(5)
                continue
            try:
                records = await _otp_thread(self._fetch_records)

                if records is None:
                    self._log.info(f"{self.panel_name}: Session expired — re-logging in …")
                    # Wait if in manual-only mode (set after Session Cleanup)
                    while self._running and getattr(self, '_manual_only', False):
                        await asyncio.sleep(5)
                    if not self._running:
                        return
                    ok = await _otp_thread(self._login)
                    if ok:
                        await _otp_thread(_update_panel_status, self.panel_name, True)
                    await asyncio.sleep(self.interval)
                    continue
                await _otp_thread(_update_panel_status, self.panel_name, True, len(records))

                # Always update _latest_record with the most recent SMS from each poll
                if records:
                    _r0 = records[0]
                    uid0 = hashlib.md5(f"{_r0['datetime']}:{_r0['number']}:{_r0['sms_body']}".encode()).hexdigest()
                    self._latest_record = {
                        'id': uid0, 'datetime': _r0['datetime'], 'number': _r0['number'],
                        'website': _r0['website'] or _detect_website_from_body(_r0['sms_body']),
                        'country': _extract_country(_r0['range_name']),
                        'otp': _extract_otp(_r0['sms_body']), 'message': _r0['sms_body'],
                        'received_at': _r0['datetime'], 'panel_name': self.panel_name,
                    }

                for rec in records:
                    dt_str     = rec['datetime']
                    range_name = rec['range_name']
                    number     = rec['number']
                    website    = rec['website']
                    sms_body   = rec['sms_body']

                    delivery_key = hashlib.sha256(
                        f"{self.panel_name}:{dt_str}:{number}:{sms_body}".encode()
                    ).hexdigest()

                    if delivery_key in self._seen_keys:
                        continue
                    already = await _otp_thread(_is_otp_delivered, delivery_key)
                    if already:
                        self._seen_keys.add(delivery_key)
                        continue

                    country = _extract_country(range_name)
                    otp     = _extract_otp(sms_body)

                    self._log.info(
                        f"{self.panel_name}: NEW SMS — website={website}, "
                        f"number=+{number}, otp={otp or '—'}"
                    )
                    await _otp_thread(_mark_otp_delivered, delivery_key)
                    self._seen_keys.add(delivery_key)

                    if not self._is_first_poll:
                        await self._notify_user(bot, number, website, otp, sms_body, delivery_key)
                        try:
                            grp_text, grp_markup = _build_group_notify_text(number, country, website, otp, sms_body)
                            await _broadcast_to_groups(bot, self.panel_name, grp_text, grp_markup,
                                                       dt_str=dt_str, number=number, sms_body=sms_body)
                        except Exception as _ge:
                            self._log.warning(f"{self.panel_name}: group notify failed — {_ge}")

                self._is_first_poll = False

            except Exception as exc:
                self._log.error(f"{self.panel_name}: Unexpected error — {exc}")

            await asyncio.sleep(self.interval)

    def start(self, bot):
        self._running = True
        self._task    = asyncio.create_task(self._loop(bot))
        self._log.info(f"{self.panel_name}: Task created.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        self._log.info(f"{self.panel_name}: Stopped.")


# ══════════════════════════════════════════════════════════════════════════════
# Number Panel Monitor  (sesskey-based, /client/ path, 17-second interval)
# ══════════════════════════════════════════════════════════════════════════════

class NumberPanelMonitor:
    """
    Background monitor for Number Panel (http://51.89.99.105/NumberPanel).
    - Login: captcha + username/password  (same form as SMS Hadi)
    - Sesskey extracted from /client/SMSCDRStats page directly
    - AJAX: /client/res/data_smscdr.php?...&sesskey=...
    - Columns: [0]datetime [1]range [2]number [3]CLI [4]sms_body [5]currency [6]payout
    - Website detected from sms_body
    - Polls every NUMBER_PANEL_INTERVAL (17) seconds
    """

    def __init__(self):
        self.panel_name     = 'Number Panel'
        self.interval       = NUMBER_PANEL_INTERVAL
        self.retry_interval = 60
        self._running       = False
        self._task          = None
        self._seen_keys: set[str] = set()
        self._is_first_poll = True
        self.session: requests.Session | None = None
        self.logged_in      = False
        self._sesskey       = None
        self._manual_only   = False
        self._latest_record = None   # cached latest SMS for get_latest_today()
        self._log           = logging.getLogger('otp_monitor.Number Panel')
        # read credentials from DB at runtime (allows admin panel changes)
        self._username      = NUMBER_PANEL_USERNAME
        self._password      = NUMBER_PANEL_PASSWORD

    def set_interval(self, seconds: int):
        """Update the polling interval live (no restart needed)."""
        self.interval = max(1, int(seconds))

    def set_retry_interval(self, seconds: int):
        """Update the login-retry interval live (no restart needed)."""
        self.retry_interval = max(1, int(seconds))

    def _refresh_credentials(self):
        try:
            from database import _get_panel_by_name
            p = _get_panel_by_name('Number Panel')
            if p:
                self._username = p['username']
                self._password = p['password']
        except Exception:
            pass

    def _login(self) -> bool:
        self._refresh_credentials()
        if not self._username or not self._password:
            self._log.warning("Number Panel: credentials not set.")
            return False
        last_reason = "unknown"
        for attempt in range(1, _LOGIN_FAST_RETRIES + 1):
            try:
                # Fresh Session Reload — drop any old cookies and rebuild
                self.session = _new_session()
                r1 = self.session.get(NUMBER_PANEL_LOGIN_URL, timeout=15)
                captcha = _solve_captcha(r1.text)
                if captcha is None:
                    last_reason = "captcha unsolvable"
                    self._log.warning(
                        f"Number Panel: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self._log.info(
                    f"Number Panel: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — Captcha solved → {captcha}"
                )
                r2 = self.session.post(
                    NUMBER_PANEL_SIGNIN_URL,
                    data={'username': self._username, 'password': self._password, 'capt': captcha},
                    headers={'Referer': NUMBER_PANEL_LOGIN_URL},
                    timeout=15, allow_redirects=True,
                )
                if 'login' in r2.url.lower():
                    last_reason = "login rejected"
                    self._log.warning(
                        f"Number Panel: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self.logged_in = True
                self._log.info(
                    f"Number Panel: Logged in successfully (attempt {attempt}/{_LOGIN_FAST_RETRIES})."
                )
                return True
            except Exception as exc:
                last_reason = f"exception: {exc}"
                self._log.warning(
                    f"Number Panel: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}"
                )
                if attempt < _LOGIN_FAST_RETRIES:
                    time.sleep(_LOGIN_RETRY_DELAY)
        self._log.error(
            f"Number Panel: All {_LOGIN_FAST_RETRIES} login attempts failed — {last_reason}"
        )
        self.logged_in = False
        return False

    def _extract_sesskey(self) -> bool:
        try:
            r = self.session.get(NUMBER_PANEL_STATS_URL, timeout=20)
            if 'login' in r.url.lower():
                self.logged_in = False
                return False
            m = re.search(
                r'"sAjaxSource"\s*:\s*"res/data_smscdr\.php[^"]*sesskey=([^"&]+)"',
                r.text
            )
            if not m:
                self._log.error("Number Panel: sesskey not found in stats page.")
                return False
            self._sesskey = m.group(1)
            self._log.info("Number Panel: sesskey extracted successfully.")
            return True
        except Exception as exc:
            self._log.error(f"Number Panel: _extract_sesskey error — {exc}")
            return False

    def _fetch_records(self) -> list[dict] | None:
        if not self._sesskey:
            return None
        try:
            now = datetime.now()
            d1  = (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00')
            d2  = now.strftime('%Y-%m-%d 23:59:59')
            params = urlencode({
                'fdate1': d1, 'fdate2': d2,
                'frange': '', 'fnum': '', 'fcli': '',
                'fgdate': '', 'fgmonth': '', 'fgrange': '',
                'fgnumber': '', 'fgcli': '',
                'fg': '0',
                'sesskey': self._sesskey,
                'iDisplayStart': '0',
                'iDisplayLength': '999999',
                'iSortCol_0': '0',
                'sSortDir_0': 'desc',
            })
            url = f"{NUMBER_PANEL_AJAX_URL}?{params}"
            r = self.session.get(
                url,
                headers={
                    'Referer': NUMBER_PANEL_STATS_URL,
                    'X-Requested-With': 'XMLHttpRequest',
                },
                timeout=25,
            )
            if 'login' in r.url.lower():
                self.logged_in = False
                return None
            data = r.json()
            rows = data.get('aaData', [])
            results = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 5:
                    continue
                dt_str     = str(row[0]).strip() if row[0] else ''
                range_name = str(row[1]).strip() if row[1] else ''
                number     = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
                # Skip totals/summary rows — they don't have a valid datetime
                if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                    continue
                sms_body   = str(row[4]).strip() if len(row) > 4 and row[4] else ''
                if not number or not dt_str or number == '0':
                    continue
                website = _detect_website_from_body(sms_body)
                results.append({
                    'datetime':   dt_str,
                    'range_name': range_name,
                    'number':     number,
                    'website':    website,
                    'sms_body':   sms_body,
                })
            self._log.info(f"Number Panel: Fetched {len(results)} SMS records.")
            return results
        except Exception as exc:
            self._log.error(f"Number Panel: _fetch_records error — {exc}")
            return None


    def get_latest_today(self) -> 'dict | None':
        """Return cached latest SMS; fall back to live HTTP fetch if cache is empty."""
        if self._latest_record:
            return self._latest_record
        if not self.logged_in or not self.session or not self._sesskey:
            return None
        try:
            now = datetime.now()
            params = urlencode({
                'fdate1': (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00'),
                'fdate2': now.strftime('%Y-%m-%d 23:59:59'),
                'frange': '', 'fnum': '', 'fcli': '',
                'fgdate': '', 'fgmonth': '', 'fgrange': '',
                'fgnumber': '', 'fgcli': '', 'fg': '0',
                'sesskey': self._sesskey,
                'iDisplayStart': '0', 'iDisplayLength': '999999',
                'iSortCol_0': '0', 'sSortDir_0': 'desc',
            })
            r = self.session.get(
                f"{NUMBER_PANEL_AJAX_URL}?{params}",
                headers={'Referer': NUMBER_PANEL_STATS_URL, 'X-Requested-With': 'XMLHttpRequest'},
                timeout=20,
            )
            if 'login' in r.url.lower():
                self.logged_in = False
                return None
            rows = r.json().get('aaData', [])
            valid = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 5:
                    continue
                dt_str = str(row[0]).strip() if row[0] else ''
                if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                    continue
                number = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
                if not number or number == '0':
                    continue
                sms_body = str(row[4]).strip() if len(row) > 4 and row[4] else ''
                valid.append({
                    'dt': dt_str, 'number': number,
                    'range_name': str(row[1]).strip() if row[1] else '',
                    'sms_body': sms_body,
                })
            if not valid:
                return None
            valid.sort(key=lambda x: x['dt'], reverse=True)
            rec = valid[0]
            uid = hashlib.md5(f"{rec['dt']}:{rec['number']}:{rec['sms_body']}".encode()).hexdigest()
            result = {
                'id': uid, 'datetime': rec['dt'], 'number': rec['number'],
                'website': _detect_website_from_body(rec['sms_body']),
                'country': _extract_country(rec['range_name']),
                'otp': _extract_otp(rec['sms_body']), 'message': rec['sms_body'],
                'received_at': rec['dt'], 'panel_name': 'Number Panel',
            }
            self._latest_record = result
            return result
        except Exception as exc:
            self._log.error(f"Number Panel: get_latest_today error — {exc}")
            return None

    async def _notify_user(self, bot, number: str, website: str, otp: str, sms_body: str, delivery_key: str = ''):
        try:
            from database import (
                _get_recent_user_by_number as _gub,
                _get_otp_bonus_settings,
                _has_otp_bonus_received, _record_otp_bonus,
                _get_effective_otp_bonus, _get_user_balance,
            )
            uid = await _otp_thread(_gub, number)
            if not uid:
                uid = await _otp_thread(_gub, '+' + number)
            if uid and bot:
                bonus_amount_credited = None
                new_balance = None
                if delivery_key:
                    bonus_cfg = await _otp_thread(_get_otp_bonus_settings)
                    if bonus_cfg['enabled']:
                        already = await _otp_thread(_has_otp_bonus_received, delivery_key)
                        if not already:
                            effective_amount = await _otp_thread(_get_effective_otp_bonus, number, bonus_cfg['amount'])
                            credited = await _otp_thread(
                                _record_otp_bonus, uid, delivery_key, effective_amount
                            )
                            if credited:
                                new_balance = await _otp_thread(_get_user_balance, uid)
                                bonus_amount_credited = effective_amount
                                self._log.info(f"Number Panel: OTP bonus BDT {effective_amount:.2f} credited to user {uid}")
                notify_text = _build_sms_notify_text(
                    number, website, sms_body,
                    bonus_amount=bonus_amount_credited,
                    new_balance=new_balance,
                )
                await bot.send_message(chat_id=uid, text=notify_text, parse_mode='HTML')
                self._log.info(f"Number Panel: Notified user {uid} for +{number}")
        except Exception as notify_exc:
            self._log.warning(f"Number Panel: Could not notify user — {notify_exc}")

    async def _loop(self, bot):
        from database import (_is_otp_delivered, _mark_otp_delivered,
                              _update_panel_status, _is_panel_enabled)
        self._log.info("Number Panel: Starting.")
        # ── Wait until panel is enabled before attempting login
        while self._running and (
            not _is_panel_enabled('Number Panel')
            or getattr(self, '_manual_only', False)
        ):
            await asyncio.sleep(5)
        if not self._running:
            return
        ok = await _otp_thread(self._login)
        _login_fail_notified = False
        while not ok and self._running:
            self._log.warning(f"Number Panel: Login failed — retrying in {self.retry_interval}s…")
            await _otp_thread(_update_panel_status, 'Number Panel', False, None, 'Login failed — retrying')
            if not _login_fail_notified:
                await _notify_admins_login_fail(bot, 'Number Panel')
                _login_fail_notified = True
            await asyncio.sleep(self.retry_interval)
            while self._running and not _is_panel_enabled('Number Panel'):
                await asyncio.sleep(5)
            if not self._running:
                return
            ok = await _otp_thread(self._login)
        if not self._running:
            return
        await _otp_thread(_update_panel_status, 'Number Panel', True)
        if _login_fail_notified:
            await _notify_admins_login_success(bot, 'Number Panel')
            _login_fail_notified = False
        ok = await _otp_thread(self._extract_sesskey)
        if not ok:
            self._log.error("Number Panel: Could not extract sesskey.")
            return

        while self._running:
            if not _is_panel_enabled('Number Panel') or getattr(self, '_manual_only', False):
                await asyncio.sleep(5)
                continue
            try:
                records = await _otp_thread(self._fetch_records)

                if records is None:
                    self._log.info("Number Panel: Session expired — re-logging in …")
                    # Wait if in manual-only mode (set after Session Cleanup)
                    while self._running and getattr(self, '_manual_only', False):
                        await asyncio.sleep(5)
                    if not self._running:
                        return
                    ok = await _otp_thread(self._login)
                    if ok:
                        await _otp_thread(_update_panel_status, 'Number Panel', True)
                        await _otp_thread(self._extract_sesskey)
                    await asyncio.sleep(self.interval)
                    continue
                await _otp_thread(_update_panel_status, 'Number Panel', True, len(records))

                # Always update _latest_record with the most recent SMS from each poll
                if records:
                    _r0 = records[0]
                    uid0 = hashlib.md5(f"{_r0['datetime']}:{_r0['number']}:{_r0['sms_body']}".encode()).hexdigest()
                    self._latest_record = {
                        'id': uid0, 'datetime': _r0['datetime'], 'number': _r0['number'],
                        'website': _r0['website'] or _detect_website_from_body(_r0['sms_body']),
                        'country': _extract_country(_r0['range_name']),
                        'otp': _extract_otp(_r0['sms_body']), 'message': _r0['sms_body'],
                        'received_at': _r0['datetime'], 'panel_name': 'Number Panel',
                    }

                for rec in records:
                    dt_str     = rec['datetime']
                    range_name = rec['range_name']
                    number     = rec['number']
                    website    = rec['website']
                    sms_body   = rec['sms_body']

                    delivery_key = hashlib.sha256(
                        f"NumberPanel:{dt_str}:{number}:{sms_body}".encode()
                    ).hexdigest()

                    if delivery_key in self._seen_keys:
                        continue
                    already = await _otp_thread(_is_otp_delivered, delivery_key)
                    if already:
                        self._seen_keys.add(delivery_key)
                        continue

                    country = _extract_country(range_name)
                    otp     = _extract_otp(sms_body)

                    self._log.info(
                        f"Number Panel: NEW SMS — website={website}, "
                        f"number=+{number}, otp={otp or '—'}"
                    )
                    await _otp_thread(_mark_otp_delivered, delivery_key)
                    self._seen_keys.add(delivery_key)

                    if not self._is_first_poll:
                        await self._notify_user(bot, number, website, otp, sms_body, delivery_key)
                        try:
                            grp_text, grp_markup = _build_group_notify_text(number, country, website, otp, sms_body)
                            await _broadcast_to_groups(bot, "Number Panel", grp_text, grp_markup,
                                                       dt_str=dt_str, number=number, sms_body=sms_body)
                        except Exception as _ge:
                            self._log.warning(f"Number Panel: group notify failed — {_ge}")

                self._is_first_poll = False

            except Exception as exc:
                self._log.error(f"Number Panel: Unexpected error — {exc}")

            await asyncio.sleep(self.interval)

    def start(self, bot):
        self._running = True
        self._task    = asyncio.create_task(self._loop(bot))
        self._log.info("Number Panel: Task created.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        self._log.info("Number Panel: Stopped.")


# ══════════════════════════════════════════════════════════════════════════════
# Purple SMS Panel Monitor  (cookie-based, /sms/dialer/ path, no sesskey)
# Columns: [0]Date [1]Termination [2]Number [3]CLI [4]Currency
#          [5]Payterm [6]Payout [7]Message
# ══════════════════════════════════════════════════════════════════════════════

class PurpleSmsMonitor:
    """
    Cookie-based monitor for Purple SMS panel.
    Login → poll ajax/dt_reports.php every interval seconds.
    No sesskey needed — session cookie is sufficient.
    Columns: [0]Date [1]Termination [2]Number [3]CLI [4]Currency
             [5]Payterm [6]Payout [7]Message
    """

    def __init__(self, interval: int = SMS_MONITOR_INTERVAL):
        self.panel_name   = 'Purple sms'
        self.base_url     = PURPLE_SMS_BASE
        self.login_url    = PURPLE_SMS_LOGIN_URL
        self.signin_url   = PURPLE_SMS_SIGNIN_URL
        self.ajax_url     = f"{PURPLE_SMS_BASE}/dialer/ajax/dt_reports.php"
        self.stats_url    = PURPLE_SMS_STATS_URL
        self.username     = PURPLE_SMS_USERNAME
        self.password     = PURPLE_SMS_PASSWORD
        self.interval       = interval
        self.retry_interval = 60
        self._log           = logging.getLogger('otp_monitor.Purple sms')
        self._running       = False
        self._task          = None
        self._seen_keys: set[str] = set()
        self._is_first_poll = True
        self.session: requests.Session | None = None
        self.logged_in      = False
        self._manual_only   = False
        self._latest_record = None   # cached latest SMS for get_latest_today()

    def set_interval(self, seconds: int):
        """Update the polling interval live (no restart needed)."""
        self.interval = max(1, int(seconds))

    def set_retry_interval(self, seconds: int):
        """Update the login-retry interval live (no restart needed)."""
        self.retry_interval = max(1, int(seconds))

    def _refresh_credentials(self):
        """Read latest credentials from DB so admin-panel changes take effect."""
        try:
            from database import _get_panel_by_name
            p = _get_panel_by_name(self.panel_name)
            if p and p.get('username'):
                self.username = p['username']
                self.password = p['password']
        except Exception:
            pass

    def _login(self) -> bool:
        self._refresh_credentials()
        last_reason = "unknown"
        for attempt in range(1, _LOGIN_FAST_RETRIES + 1):
            try:
                # Fresh Session Reload — drop any old cookies and rebuild
                self.session = _new_session()
                r1 = self.session.get(self.login_url, timeout=15)
                captcha = _solve_captcha(r1.text)
                if captcha is None:
                    last_reason = "captcha unsolvable"
                    self._log.warning(
                        f"Purple sms: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self._log.info(
                    f"Purple sms: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — Captcha solved → {captcha}"
                )
                r2 = self.session.post(
                    self.signin_url,
                    data={'username': self.username, 'password': self.password, 'capt': captcha},
                    headers={'Referer': self.login_url},
                    timeout=15, allow_redirects=True,
                )
                final_lower = r2.url.lower().rstrip('/')
                final_last  = final_lower.split('/')[-1]
                if final_last in ('login', 'signin', 'sign-in') or \
                   final_last.endswith('login') or final_last.endswith('signin'):
                    last_reason = "login rejected"
                    self._log.warning(
                        f"Purple sms: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self.logged_in = True
                self._log.info(
                    f"Purple sms: Logged in successfully (attempt {attempt}/{_LOGIN_FAST_RETRIES})."
                )
                return True
            except Exception as exc:
                last_reason = f"exception: {exc}"
                self._log.warning(
                    f"Purple sms: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}"
                )
                if attempt < _LOGIN_FAST_RETRIES:
                    time.sleep(_LOGIN_RETRY_DELAY)
        self._log.error(
            f"Purple sms: All {_LOGIN_FAST_RETRIES} login attempts failed — {last_reason}"
        )
        self.logged_in = False
        return False

    def _fetch_records(self) -> list[dict] | None:
        try:
            now = datetime.now()
            d1  = (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00')
            d2  = now.strftime('%Y-%m-%d 23:59:59')
            params = {
                'fdate1': d1, 'fdate2': d2,
                'ftermination': '', 'fnum': '', 'fcli': '',
                'fgdate': '0', 'fgtermination': '0',
                'fgnumber': '0', 'fgcli': '0', 'fg': '0',
                'iDisplayStart': '0', 'iDisplayLength': '999999',
                'iSortCol_0': '0', 'sSortDir_0': 'desc',
            }
            r = self.session.get(
                self.ajax_url,
                params=params,
                headers={
                    'Referer': self.stats_url,
                    'X-Requested-With': 'XMLHttpRequest',
                },
                timeout=25,
            )
            if 'login' in r.url.lower() or 'signin' in r.url.lower():
                self.logged_in = False
                return None
            data = r.json()
            rows = data.get('aaData', [])
            results = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 8:
                    continue
                dt_str     = str(row[0]).strip() if row[0] else ''
                range_name = str(row[1]).strip() if row[1] else ''
                number     = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
                # Skip totals/summary rows — they don't have a valid datetime
                if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                    continue
                sms_body   = str(row[7]).strip() if row[7] else ''
                if not number or not dt_str:
                    continue
                website = _detect_website_from_body(sms_body)
                results.append({
                    'datetime':   dt_str,
                    'range_name': range_name,
                    'number':     number,
                    'website':    website,
                    'sms_body':   sms_body,
                })
            self._log.info(f"Purple sms: Fetched {len(results)} SMS records.")
            return results
        except Exception as exc:
            self._log.error(f"Purple sms: _fetch_records error — {exc}")
            return None


    def get_latest_today(self) -> 'dict | None':
        """Return cached latest SMS; fall back to live HTTP fetch if cache is empty."""
        if self._latest_record:
            return self._latest_record
        if not self.logged_in or not self.session:
            return None
        try:
            now = datetime.now()
            params = {
                'fdate1': (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00'),
                'fdate2': now.strftime('%Y-%m-%d 23:59:59'),
                'ftermination': '', 'fnum': '', 'fcli': '',
                'fgdate': '0', 'fgtermination': '0',
                'fgnumber': '0', 'fgcli': '0', 'fg': '0',
                'iDisplayStart': '0', 'iDisplayLength': '999999',
                'iSortCol_0': '0', 'sSortDir_0': 'desc',
            }
            r = self.session.get(
                self.ajax_url,
                params=params,
                headers={'Referer': self.stats_url, 'X-Requested-With': 'XMLHttpRequest'},
                timeout=20,
            )
            if 'login' in r.url.lower() or 'signin' in r.url.lower():
                self.logged_in = False
                return None
            rows = r.json().get('aaData', [])
            valid = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 8:
                    continue
                dt_str = str(row[0]).strip() if row[0] else ''
                if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                    continue
                number = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
                if not number:
                    continue
                sms_body = str(row[7]).strip() if row[7] else ''
                valid.append({
                    'dt': dt_str, 'number': number,
                    'range_name': str(row[1]).strip() if row[1] else '',
                    'sms_body': sms_body,
                })
            if not valid:
                return None
            valid.sort(key=lambda x: x['dt'], reverse=True)
            rec = valid[0]
            uid = hashlib.md5(f"{rec['dt']}:{rec['number']}:{rec['sms_body']}".encode()).hexdigest()
            result = {
                'id': uid, 'datetime': rec['dt'], 'number': rec['number'],
                'website': _detect_website_from_body(rec['sms_body']),
                'country': _extract_country(rec['range_name']),
                'otp': _extract_otp(rec['sms_body']), 'message': rec['sms_body'],
                'received_at': rec['dt'], 'panel_name': 'Purple sms',
            }
            self._latest_record = result
            return result
        except Exception as exc:
            self._log.error(f"Purple sms: get_latest_today error — {exc}")
            return None

    async def _notify_user(self, bot, number: str, website: str, otp: str, sms_body: str, delivery_key: str = ''):
        try:
            from database import (
                _get_recent_user_by_number as _gub,
                _get_otp_bonus_settings,
                _has_otp_bonus_received, _record_otp_bonus,
                _get_effective_otp_bonus, _get_user_balance,
            )
            uid = await _otp_thread(_gub, number)
            if not uid:
                uid = await _otp_thread(_gub, '+' + number)
            if uid and bot:
                bonus_amount_credited = None
                new_balance = None
                if delivery_key:
                    bonus_cfg = await _otp_thread(_get_otp_bonus_settings)
                    if bonus_cfg['enabled']:
                        already = await _otp_thread(_has_otp_bonus_received, delivery_key)
                        if not already:
                            effective_amount = await _otp_thread(_get_effective_otp_bonus, number, bonus_cfg['amount'])
                            credited = await _otp_thread(
                                _record_otp_bonus, uid, delivery_key, effective_amount
                            )
                            if credited:
                                new_balance = await _otp_thread(_get_user_balance, uid)
                                bonus_amount_credited = effective_amount
                notify_text = _build_sms_notify_text(
                    number, website, sms_body,
                    bonus_amount=bonus_amount_credited,
                    new_balance=new_balance,
                )
                await bot.send_message(chat_id=uid, text=notify_text, parse_mode='HTML')
                self._log.info(f"Purple sms: Notified user {uid} for +{number}")
        except Exception as notify_exc:
            self._log.warning(f"Purple sms: Could not notify user — {notify_exc}")

    async def _loop(self, bot):
        from database import (_is_otp_delivered, _mark_otp_delivered,
                              _update_panel_status, _is_panel_enabled)
        self._log.info("Purple sms: Starting.")
        # ── Wait until panel is enabled before attempting login
        while self._running and (
            not _is_panel_enabled('Purple sms')
            or getattr(self, '_manual_only', False)
        ):
            await asyncio.sleep(5)
        if not self._running:
            return
        ok = await _otp_thread(self._login)
        _login_fail_notified = False
        while not ok and self._running:
            self._log.warning(f"Purple sms: Login failed — retrying in {self.retry_interval}s…")
            await _otp_thread(_update_panel_status, 'Purple sms', False, None, 'Login failed — retrying')
            if not _login_fail_notified:
                await _notify_admins_login_fail(bot, 'Purple sms')
                _login_fail_notified = True
            await asyncio.sleep(self.retry_interval)
            while self._running and not _is_panel_enabled('Purple sms'):
                await asyncio.sleep(5)
            if not self._running:
                return
            ok = await _otp_thread(self._login)
        if not self._running:
            return
        await _otp_thread(_update_panel_status, 'Purple sms', True)
        if _login_fail_notified:
            await _notify_admins_login_success(bot, 'Purple sms')
            _login_fail_notified = False

        while self._running:
            if not _is_panel_enabled('Purple sms') or getattr(self, '_manual_only', False):
                await asyncio.sleep(5)
                continue
            try:
                records = await _otp_thread(self._fetch_records)

                if records is None:
                    self._log.info("Purple sms: Session expired — re-logging in …")
                    # Wait if in manual-only mode (set after Session Cleanup)
                    while self._running and getattr(self, '_manual_only', False):
                        await asyncio.sleep(5)
                    if not self._running:
                        return
                    ok = await _otp_thread(self._login)
                    if ok:
                        await _otp_thread(_update_panel_status, 'Purple sms', True)
                    await asyncio.sleep(self.interval)
                    continue
                await _otp_thread(_update_panel_status, 'Purple sms', True, len(records))

                # Always update _latest_record with the most recent SMS from each poll
                if records:
                    _r0 = records[0]
                    uid0 = hashlib.md5(f"{_r0['datetime']}:{_r0['number']}:{_r0['sms_body']}".encode()).hexdigest()
                    self._latest_record = {
                        'id': uid0, 'datetime': _r0['datetime'], 'number': _r0['number'],
                        'website': _r0['website'] or _detect_website_from_body(_r0['sms_body']),
                        'country': _extract_country(_r0['range_name']),
                        'otp': _extract_otp(_r0['sms_body']), 'message': _r0['sms_body'],
                        'received_at': _r0['datetime'], 'panel_name': 'Purple sms',
                    }

                for rec in records:
                    dt_str     = rec['datetime']
                    range_name = rec['range_name']
                    number     = rec['number']
                    website    = rec['website']
                    sms_body   = rec['sms_body']

                    delivery_key = hashlib.sha256(
                        f"Purple sms:{dt_str}:{number}:{sms_body}".encode()
                    ).hexdigest()

                    if delivery_key in self._seen_keys:
                        continue
                    already = await _otp_thread(_is_otp_delivered, delivery_key)
                    if already:
                        self._seen_keys.add(delivery_key)
                        continue

                    country = _extract_country(range_name)
                    otp     = _extract_otp(sms_body)

                    self._log.info(
                        f"Purple sms: NEW SMS — website={website}, "
                        f"number=+{number}, otp={otp or '—'}"
                    )
                    await _otp_thread(_mark_otp_delivered, delivery_key)
                    self._seen_keys.add(delivery_key)

                    if not self._is_first_poll:
                        await self._notify_user(bot, number, website, otp, sms_body, delivery_key)
                        try:
                            grp_text, grp_markup = _build_group_notify_text(number, country, website, otp, sms_body)
                            await _broadcast_to_groups(bot, "Purple sms", grp_text, grp_markup,
                                                       dt_str=dt_str, number=number, sms_body=sms_body)
                        except Exception as _ge:
                            self._log.warning(f"Purple sms: group notify failed — {_ge}")

                self._is_first_poll = False

            except Exception as exc:
                self._log.error(f"Purple sms: Unexpected error — {exc}")

            await asyncio.sleep(self.interval)

    def start(self, bot):
        self._running = True
        self._task    = asyncio.create_task(self._loop(bot))
        self._log.info("Purple sms: Task created.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        self._log.info("Purple sms: Stopped.")


# ══════════════════════════════════════════════════════════════════════════════
# Generic Sesskey-based Panel Monitor  (configurable, for future use)
# ══════════════════════════════════════════════════════════════════════════════

class GenericSessKeyMonitor:   # reserved for future sesskey-based panels
    """
    Generic background monitor for sesskey-based panels with configurable URLs.
    Login → extract sesskey from stats page → poll AJAX every interval seconds.
    Column order: [0]datetime [1]range [2]number [3]CLI/website [4]sms_body ...
    Website detected from col[3] if present, otherwise from SMS body text.
    """

    def __init__(
        self,
        panel_name: str,
        login_url: str,
        signin_url: str,
        stats_url: str,
        ajax_url: str,
        username: str,
        password: str,
        interval: int = 3,
    ):
        self.panel_name  = panel_name
        self.login_url   = login_url
        self.signin_url  = signin_url
        self.stats_url   = stats_url
        self.ajax_url    = ajax_url
        self.username    = username
        self.password    = password
        self.interval       = interval
        self.retry_interval = 60
        self._log           = logging.getLogger(f"otp_monitor.{panel_name}")
        self._running       = False
        self._task          = None
        self._seen_keys: set[str] = set()
        self._is_first_poll = True
        self.session: requests.Session | None = None
        self.logged_in      = False
        self._sesskey       = None
        self._latest_record = None   # cached latest SMS for get_latest_today()

    def set_interval(self, seconds: int):
        """Update the polling interval live (no restart needed)."""
        self.interval = max(1, int(seconds))

    def set_retry_interval(self, seconds: int):
        """Update the login-retry interval live (no restart needed)."""
        self.retry_interval = max(1, int(seconds))

    def _refresh_credentials(self):
        """Read latest credentials from DB so admin-panel changes take effect."""
        try:
            from database import _get_panel_by_name
            p = _get_panel_by_name(self.panel_name)
            if p and p.get('username'):
                self.username = p['username']
                self.password = p['password']
        except Exception:
            pass

    def _login(self) -> bool:
        self._refresh_credentials()
        if not self.username or not self.password:
            self._log.warning(f"{self.panel_name}: credentials not set.")
            return False
        last_reason = "unknown"
        for attempt in range(1, _LOGIN_FAST_RETRIES + 1):
            try:
                # Fresh Session Reload — drop any old cookies and rebuild
                self.session = _new_session()
                r1 = self.session.get(self.login_url, timeout=15)
                captcha = _solve_captcha(r1.text)
                if captcha is None:
                    last_reason = "captcha unsolvable"
                    self._log.warning(
                        f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self._log.info(
                    f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — Captcha solved → {captcha}"
                )
                r2 = self.session.post(
                    self.signin_url,
                    data={'username': self.username, 'password': self.password, 'capt': captcha},
                    headers={'Referer': self.login_url},
                    timeout=15, allow_redirects=True,
                )
                final_lower = r2.url.lower().rstrip('/')
                final_last  = final_lower.split('/')[-1]
                if final_last in ('login', 'signin', 'sign-in') or \
                   final_last.endswith('login') or final_last.endswith('signin'):
                    last_reason = "login rejected"
                    self._log.warning(
                        f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self.logged_in = True
                self._log.info(
                    f"{self.panel_name}: Logged in successfully (attempt {attempt}/{_LOGIN_FAST_RETRIES})."
                )
                return True
            except Exception as exc:
                last_reason = f"exception: {exc}"
                self._log.warning(
                    f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}"
                )
                if attempt < _LOGIN_FAST_RETRIES:
                    time.sleep(_LOGIN_RETRY_DELAY)
        self._log.error(
            f"{self.panel_name}: All {_LOGIN_FAST_RETRIES} login attempts failed — {last_reason}"
        )
        self.logged_in = False
        return False

    def _extract_sesskey(self) -> bool:
        try:
            r = self.session.get(self.stats_url, timeout=20)
            if 'login' in r.url.lower():
                self.logged_in = False
                return False
            m = re.search(
                r'"sAjaxSource"\s*:\s*"res/data_smscdr\.php[^"]*sesskey=([^"&]+)"',
                r.text
            )
            if not m:
                self._log.error(f"{self.panel_name}: sesskey not found in stats page.")
                return False
            self._sesskey = m.group(1)
            self._log.info(f"{self.panel_name}: sesskey extracted successfully.")
            return True
        except Exception as exc:
            self._log.error(f"{self.panel_name}: _extract_sesskey error — {exc}")
            return False

    def _fetch_records(self) -> list[dict] | None:
        if not self._sesskey:
            return None
        try:
            now = datetime.now()
            d1  = (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00')
            d2  = now.strftime('%Y-%m-%d 23:59:59')
            params = urlencode({
                'fdate1': d1, 'fdate2': d2,
                'frange': '', 'fnum': '', 'fcli': '',
                'fgdate': '', 'fgmonth': '', 'fgrange': '',
                'fgnumber': '', 'fgcli': '',
                'fg': '0',
                'sesskey': self._sesskey,
                'iDisplayStart': '0',
                'iDisplayLength': '999999',
            })
            url = f"{self.ajax_url}?{params}"
            r = self.session.get(
                url,
                headers={
                    'Referer': self.stats_url,
                    'X-Requested-With': 'XMLHttpRequest',
                },
                timeout=25,
            )
            if 'login' in r.url.lower():
                self.logged_in = False
                return None
            data = r.json()
            rows = data.get('aaData', [])
            results = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 3:
                    continue
                dt_str     = str(row[0]).strip() if row[0] else ''
                range_name = str(row[1]).strip() if row[1] else ''
                number     = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
                # Skip totals/summary rows — they don't have a valid datetime
                if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                    continue
                website    = ''
                sms_body   = ''
                if len(row) > 5:
                    website  = str(row[3]).strip() if row[3] else ''
                    sms_body = str(row[5]).strip() if row[5] else ''
                elif len(row) > 4:
                    sms_body = str(row[4]).strip() if row[4] else ''
                detected = _detect_website_from_body(sms_body)
                if detected and detected != 'Unknown':
                    website = detected
                elif not website or website.lower() in ('unknown', '', 'none'):
                    website = detected
                if not number or not dt_str:
                    continue
                results.append({
                    'datetime':   dt_str,
                    'range_name': range_name,
                    'number':     number,
                    'website':    website,
                    'sms_body':   sms_body,
                })
            self._log.info(f"{self.panel_name}: Fetched {len(results)} SMS records.")
            return results
        except Exception as exc:
            self._log.error(f"{self.panel_name}: _fetch_records error — {exc}")
            return None


    def get_latest_today(self) -> 'dict | None':
        """Return cached latest SMS; fall back to live HTTP fetch if cache is empty."""
        if self._latest_record:
            return self._latest_record
        if not self.logged_in or not self.session or not self._sesskey:
            return None
        try:
            now = datetime.now()
            params = urlencode({
                'fdate1': (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00'),
                'fdate2': now.strftime('%Y-%m-%d 23:59:59'),
                'frange': '', 'fnum': '', 'fcli': '',
                'fgdate': '', 'fgmonth': '', 'fgrange': '',
                'fgnumber': '', 'fgcli': '', 'fg': '0',
                'sesskey': self._sesskey,
                'iDisplayStart': '0', 'iDisplayLength': '999999',
            })
            r = self.session.get(
                f"{self.ajax_url}?{params}",
                headers={'Referer': self.stats_url, 'X-Requested-With': 'XMLHttpRequest'},
                timeout=20,
            )
            if 'login' in r.url.lower():
                self.logged_in = False
                return None
            rows = r.json().get('aaData', [])
            valid = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 3:
                    continue
                dt_str = str(row[0]).strip() if row[0] else ''
                if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                    continue
                number = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
                if not number:
                    continue
                if len(row) > 5:
                    sms_body = str(row[5]).strip() if row[5] else ''
                elif len(row) > 4:
                    sms_body = str(row[4]).strip() if row[4] else ''
                else:
                    sms_body = ''
                valid.append({
                    'dt': dt_str, 'number': number,
                    'range_name': str(row[1]).strip() if row[1] else '',
                    'sms_body': sms_body,
                })
            if not valid:
                return None
            valid.sort(key=lambda x: x['dt'], reverse=True)
            rec = valid[0]
            uid = hashlib.md5(f"{rec['dt']}:{rec['number']}:{rec['sms_body']}".encode()).hexdigest()
            result = {
                'id': uid, 'datetime': rec['dt'], 'number': rec['number'],
                'website': _detect_website_from_body(rec['sms_body']),
                'country': _extract_country(rec['range_name']),
                'otp': _extract_otp(rec['sms_body']), 'message': rec['sms_body'],
                'received_at': rec['dt'], 'panel_name': self.panel_name,
            }
            self._latest_record = result
            return result
        except Exception as exc:
            self._log.error(f"{self.panel_name}: get_latest_today error — {exc}")
            return None

    async def _notify_user(self, bot, number: str, website: str, otp: str, sms_body: str, delivery_key: str = ''):
        try:
            from database import (
                _get_recent_user_by_number as _gub,
                _get_otp_bonus_settings,
                _has_otp_bonus_received, _record_otp_bonus,
                _get_effective_otp_bonus, _get_user_balance,
            )
            uid = await _otp_thread(_gub, number)
            if not uid:
                uid = await _otp_thread(_gub, '+' + number)
            if uid and bot:
                bonus_amount_credited = None
                new_balance = None
                if delivery_key:
                    bonus_cfg = await _otp_thread(_get_otp_bonus_settings)
                    if bonus_cfg['enabled']:
                        already = await _otp_thread(_has_otp_bonus_received, delivery_key)
                        if not already:
                            effective_amount = await _otp_thread(_get_effective_otp_bonus, number, bonus_cfg['amount'])
                            credited = await _otp_thread(
                                _record_otp_bonus, uid, delivery_key, effective_amount
                            )
                            if credited:
                                new_balance = await _otp_thread(_get_user_balance, uid)
                                bonus_amount_credited = effective_amount
                notify_text = _build_sms_notify_text(
                    number, website, sms_body,
                    bonus_amount=bonus_amount_credited,
                    new_balance=new_balance,
                )
                await bot.send_message(chat_id=uid, text=notify_text, parse_mode='HTML')
                self._log.info(f"{self.panel_name}: Notified user {uid} for +{number}")
        except Exception as notify_exc:
            self._log.warning(f"{self.panel_name}: Could not notify user — {notify_exc}")

    async def _loop(self, bot):
        from database import (_is_otp_delivered, _mark_otp_delivered,
                              _update_panel_status, _is_panel_enabled)
        self._log.info(f"{self.panel_name}: Starting.")
        # ── Wait until panel is enabled before attempting login
        while self._running and (
            not _is_panel_enabled(self.panel_name)
            or getattr(self, '_manual_only', False)
        ):
            await asyncio.sleep(5)
        if not self._running:
            return
        ok = await _otp_thread(self._login)
        _login_fail_notified = False
        while not ok and self._running:
            self._log.warning(f"{self.panel_name}: Login failed — retrying in {self.retry_interval}s…")
            await _otp_thread(_update_panel_status, self.panel_name, False, None, 'Login failed — retrying')
            if not _login_fail_notified:
                await _notify_admins_login_fail(bot, self.panel_name)
                _login_fail_notified = True
            await asyncio.sleep(self.retry_interval)
            while self._running and (
                not _is_panel_enabled(self.panel_name)
                or getattr(self, '_manual_only', False)
            ):
                await asyncio.sleep(5)
            if not self._running:
                return
            ok = await _otp_thread(self._login)
        if not self._running:
            return
        await _otp_thread(_update_panel_status, self.panel_name, True)
        if _login_fail_notified:
            await _notify_admins_login_success(bot, self.panel_name)
            _login_fail_notified = False
        ok = await _otp_thread(self._extract_sesskey)
        if not ok:
            self._log.error(f"{self.panel_name}: Could not extract sesskey.")
            return

        while self._running:
            if not _is_panel_enabled(self.panel_name) or getattr(self, '_manual_only', False):
                await asyncio.sleep(5)
                continue
            try:
                records = await _otp_thread(self._fetch_records)

                if records is None:
                    self._log.info(f"{self.panel_name}: Session expired — re-logging in …")
                    # Wait if in manual-only mode (set after Session Cleanup)
                    while self._running and getattr(self, '_manual_only', False):
                        await asyncio.sleep(5)
                    if not self._running:
                        return
                    ok = await _otp_thread(self._login)
                    if ok:
                        await _otp_thread(_update_panel_status, self.panel_name, True)
                        await _otp_thread(self._extract_sesskey)
                    await asyncio.sleep(self.interval)
                    continue
                await _otp_thread(_update_panel_status, self.panel_name, True, len(records))

                # Always update _latest_record with the most recent SMS from each poll
                if records:
                    _r0 = records[0]
                    uid0 = hashlib.md5(f"{_r0['datetime']}:{_r0['number']}:{_r0['sms_body']}".encode()).hexdigest()
                    self._latest_record = {
                        'id': uid0, 'datetime': _r0['datetime'], 'number': _r0['number'],
                        'website': _r0['website'] or _detect_website_from_body(_r0['sms_body']),
                        'country': _extract_country(_r0['range_name']),
                        'otp': _extract_otp(_r0['sms_body']), 'message': _r0['sms_body'],
                        'received_at': _r0['datetime'], 'panel_name': self.panel_name,
                    }

                for rec in records:
                    dt_str     = rec['datetime']
                    range_name = rec['range_name']
                    number     = rec['number']
                    website    = rec['website']
                    sms_body   = rec['sms_body']

                    delivery_key = hashlib.sha256(
                        f"{self.panel_name}:{dt_str}:{number}:{sms_body}".encode()
                    ).hexdigest()

                    if delivery_key in self._seen_keys:
                        continue
                    already = await _otp_thread(_is_otp_delivered, delivery_key)
                    if already:
                        self._seen_keys.add(delivery_key)
                        continue

                    country = _extract_country(range_name)
                    otp     = _extract_otp(sms_body)

                    self._log.info(
                        f"{self.panel_name}: NEW SMS — website={website}, "
                        f"number=+{number}, otp={otp or '—'}"
                    )
                    await _otp_thread(_mark_otp_delivered, delivery_key)
                    self._seen_keys.add(delivery_key)

                    if not self._is_first_poll:
                        await self._notify_user(bot, number, website, otp, sms_body, delivery_key)
                        try:
                            grp_text, grp_markup = _build_group_notify_text(number, country, website, otp, sms_body)
                            await _broadcast_to_groups(bot, self.panel_name, grp_text, grp_markup,
                                                       dt_str=dt_str, number=number, sms_body=sms_body)
                        except Exception as _ge:
                            self._log.warning(f"{self.panel_name}: group notify failed — {_ge}")

                self._is_first_poll = False

            except Exception as exc:
                self._log.error(f"{self.panel_name}: Unexpected error — {exc}")

            await asyncio.sleep(self.interval)

    def start(self, bot):
        self._running = True
        self._task    = asyncio.create_task(self._loop(bot))
        self._log.info(f"{self.panel_name}: Task created.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        self._log.info(f"{self.panel_name}: Stopped.")


# ══════════════════════════════════════════════════════════════════════════════
# Wolf SMS — ClientPanelMonitor subclass (no captcha, /agent/ path)
# ══════════════════════════════════════════════════════════════════════════════

class WolfSmsMonitor(ClientPanelMonitor):
    """Wolf SMS panel: reCAPTCHA present on login page but NOT enforced server-
    side.  We POST username/password directly — no captcha solving needed."""

    def _login(self) -> bool:
        if not self.username or not self.password:
            self._log.warning(f"{self.panel_name}: credentials not set.")
            return False
        last_reason = "unknown"
        for attempt in range(1, _LOGIN_FAST_RETRIES + 1):
            try:
                self.session = _new_session()
                # Skip captcha — server accepts bare username/password POST
                r2 = self.session.post(
                    self.signin_url,
                    data={'username': self.username, 'password': self.password},
                    headers={'Referer': self.login_page},
                    timeout=15, allow_redirects=True,
                )
                final_path = r2.url.lower()
                final_last = final_path.rstrip('/').split('/')[-1]
                if final_last in ('login', 'signin', 'sign-in') or \
                   final_last.endswith('login') or final_last.endswith('signin'):
                    last_reason = "login rejected"
                    self._log.warning(
                        f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self.logged_in = True
                self._log.info(
                    f"{self.panel_name}: Logged in successfully (attempt {attempt}/{_LOGIN_FAST_RETRIES})."
                )
                return True
            except Exception as exc:
                last_reason = f"exception: {exc}"
                self._log.warning(
                    f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}"
                )
                if attempt < _LOGIN_FAST_RETRIES:
                    time.sleep(_LOGIN_RETRY_DELAY)
        self._log.error(
            f"{self.panel_name}: All {_LOGIN_FAST_RETRIES} login attempts failed — {last_reason}"
        )
        self.logged_in = False
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Shark SMS — GenericSessKeyMonitor subclass (math captcha + crlf hidden field)
# ══════════════════════════════════════════════════════════════════════════════

class SharkSmsMonitor(GenericSessKeyMonitor):
    """Shark SMS panel: math captcha (name=capt) + hidden crlf='' field on login,
    then sesskey extracted from the stats page AJAX source."""

    def _login(self) -> bool:
        if not self.username or not self.password:
            self._log.warning(f"{self.panel_name}: credentials not set.")
            return False
        last_reason = "unknown"
        for attempt in range(1, _LOGIN_FAST_RETRIES + 1):
            try:
                self.session = _new_session()
                r1 = self.session.get(self.login_url, timeout=15)
                captcha = _solve_captcha(r1.text)
                if captcha is None:
                    last_reason = "captcha unsolvable"
                    self._log.warning(
                        f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self._log.info(
                    f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — Captcha solved → {captcha}"
                )
                r2 = self.session.post(
                    self.signin_url,
                    data={
                        'username': self.username,
                        'password': self.password,
                        'capt': captcha,
                        'crlf': '',
                    },
                    headers={'Referer': self.login_url},
                    timeout=15, allow_redirects=True,
                )
                final_lower = r2.url.lower().rstrip('/')
                final_last  = final_lower.split('/')[-1]
                if final_last in ('login', 'signin', 'sign-in') or \
                   final_last.endswith('login') or final_last.endswith('signin'):
                    last_reason = "login rejected"
                    self._log.warning(
                        f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}."
                    )
                    if attempt < _LOGIN_FAST_RETRIES:
                        time.sleep(_LOGIN_RETRY_DELAY)
                    continue
                self.logged_in = True
                self._log.info(
                    f"{self.panel_name}: Logged in successfully (attempt {attempt}/{_LOGIN_FAST_RETRIES})."
                )
                return True
            except Exception as exc:
                last_reason = f"exception: {exc}"
                self._log.warning(
                    f"{self.panel_name}: Attempt {attempt}/{_LOGIN_FAST_RETRIES} — {last_reason}"
                )
                if attempt < _LOGIN_FAST_RETRIES:
                    time.sleep(_LOGIN_RETRY_DELAY)
        self._log.error(
            f"{self.panel_name}: All {_LOGIN_FAST_RETRIES} login attempts failed — {last_reason}"
        )
        self.logged_in = False
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Module-level singletons
# ══════════════════════════════════════════════════════════════════════════════

# SMS Hadi monitor (sesskey-based, /agent/ path — credentials are agent-type)
monitor = OTPMonitor()

# SMS Hadi 2 monitor (second account — same server, different credentials)
sms_hadi2_monitor = OTPMonitor2()

# Konekta Premium monitor (cookie-based, login at /sign-in)
konekta_monitor = ClientPanelMonitor(
    panel_name    = 'Konekta Premium',
    base_url      = KONEKTA_BASE,
    login_page_url= KONEKTA_LOGIN_URL,
    signin_url    = KONEKTA_SIGNIN_URL,
    username      = KONEKTA_USERNAME,
    password      = KONEKTA_PASSWORD,
)

# MSI SMS monitor (cookie-based, login at /login)
msi_sms_monitor = ClientPanelMonitor(
    panel_name    = 'Msi sms',
    base_url      = MSI_SMS_BASE,
    login_page_url= MSI_SMS_LOGIN_URL,
    signin_url    = MSI_SMS_SIGNIN_URL,
    username      = MSI_SMS_USERNAME,
    password      = MSI_SMS_PASSWORD,
)

# Number Panel monitor (sesskey-based, /client/ path, 17s interval)
number_panel_monitor = NumberPanelMonitor()

# Purple SMS monitor (cookie-based, /sms/dialer/ajax/ path, no sesskey)
purple_sms_monitor = PurpleSmsMonitor()

# Proof SMS monitor (cookie-based, /ints/ path, 3s interval)
proof_sms_monitor = ClientPanelMonitor(
    panel_name    = 'Proof sms',
    base_url      = PROOF_SMS_BASE,
    login_page_url= PROOF_SMS_LOGIN_URL,
    signin_url    = PROOF_SMS_SIGNIN_URL,
    username      = PROOF_SMS_USERNAME,
    password      = PROOF_SMS_PASSWORD,
)

# Lamix SMS monitor (cookie-based, /ints/ path, /agent/ sub-path, 3s interval)
lamix_sms_monitor = ClientPanelMonitor(
    panel_name    = 'Lamix sms',
    base_url      = LAMIX_SMS_BASE,
    login_page_url= LAMIX_SMS_LOGIN_URL,
    signin_url    = LAMIX_SMS_SIGNIN_URL,
    username      = LAMIX_SMS_USERNAME,
    password      = LAMIX_SMS_PASSWORD,
    path_prefix   = 'agent',
)

# Seven 1 Tel monitor (cookie-based, /ints/ path, 3s interval)
seven1tel_monitor = ClientPanelMonitor(
    panel_name    = 'Seven 1 Tel',
    base_url      = SEVEN1TEL_BASE,
    login_page_url= SEVEN1TEL_LOGIN_URL,
    signin_url    = SEVEN1TEL_SIGNIN_URL,
    username      = SEVEN1TEL_USERNAME,
    password      = SEVEN1TEL_PASSWORD,
)

# Flex SMS monitor (cookie-based, /ints/agent/ path, 3s interval)
mait_sms_monitor = ClientPanelMonitor(
    panel_name    = 'Flex sms',
    base_url      = MAIT_SMS_BASE,
    login_page_url= MAIT_SMS_LOGIN_URL,
    signin_url    = MAIT_SMS_SIGNIN_URL,
    username      = MAIT_SMS_USERNAME,
    password      = MAIT_SMS_PASSWORD,
    path_prefix   = 'agent',
)

# Zento SMS monitor (cookie-based, /ints/ path, 3s interval)
zento_sms_monitor = ClientPanelMonitor(
    panel_name    = 'Zento sms',
    base_url      = ZENTO_SMS_BASE,
    login_page_url= ZENTO_SMS_LOGIN_URL,
    signin_url    = ZENTO_SMS_SIGNIN_URL,
    username      = ZENTO_SMS_USERNAME,
    password      = ZENTO_SMS_PASSWORD,
)

# Wolf SMS monitor (cookie-based, /ints/ agent path, no captcha, 3s interval)
wolf_sms_monitor = WolfSmsMonitor(
    panel_name    = 'Wolf sms',
    base_url      = WOLF_SMS_BASE,
    login_page_url= WOLF_SMS_LOGIN_URL,
    signin_url    = WOLF_SMS_SIGNIN_URL,
    username      = WOLF_SMS_USERNAME,
    password      = WOLF_SMS_PASSWORD,
    path_prefix   = 'agent',
)

# Shark SMS monitor (sesskey-based, /ints/ agent path, math captcha + crlf, 3s interval)
shark_sms_monitor = SharkSmsMonitor(
    panel_name  = 'Shark sms',
    login_url   = SHARK_SMS_LOGIN_URL,
    signin_url  = SHARK_SMS_SIGNIN_URL,
    stats_url   = SHARK_SMS_STATS_URL,
    ajax_url    = SHARK_SMS_AJAX_URL,
    username    = SHARK_SMS_USERNAME,
    password    = SHARK_SMS_PASSWORD,
    interval    = SHARK_SMS_INTERVAL,
)


# ══════════════════════════════════════════════════════════════════════════════
# Live "Latest Message" fetcher — routes to the correct monitor
# ══════════════════════════════════════════════════════════════════════════════

def get_panel_latest_today(panel_name: str) -> 'dict | None':
    """Fetch the single most recent SMS for a panel using the monitor's
    in-memory record (updated every 3 s by the background poller).
    Falls back to a live HTTP fetch if no cached record exists yet.
    Returns None if the panel is not logged in or has no SMS today.
    """
    _monitor_map = {
        'SMS Hadi':        monitor,
        'SMS Hadi 2':      sms_hadi2_monitor,
        'Konekta Premium': konekta_monitor,
        'Msi sms':         msi_sms_monitor,
        'Number Panel':    number_panel_monitor,
        'Purple sms':      purple_sms_monitor,
        'Proof sms':       proof_sms_monitor,
        'Lamix sms':       lamix_sms_monitor,
        'Seven 1 Tel':     seven1tel_monitor,
        'Flex sms':        mait_sms_monitor,
        'Zento sms':       zento_sms_monitor,
        'Wolf sms':        wolf_sms_monitor,
        'Shark sms':       shark_sms_monitor,
    }
    m = _monitor_map.get(panel_name)
    if m is None:
        return None
    try:
        return m.get_latest_today()
    except Exception as exc:
        logger.error(f"get_panel_latest_today({panel_name}): {exc}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Standalone panel data fetcher  (Admin → Panel List → Login & View Stats)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_panel_data(base_url: str, username: str, password: str) -> dict | None:
    """
    Login to any panel with given credentials and return SMS stats.
    Automatically detects which panel type to use based on the base URL.
    Returns dict {'total': int, 'records': list} or None on failure.
    """
    base_url = base_url.rstrip('/')
    # SMS Hadi can be slow to accept a fresh login while the background
    # monitor is already polling. Try the panel credentials first, then
    # fall back to the live monitor session to avoid false "connection failed"
    # messages during temporary panel timeouts.
    if 'smshadi.net' in base_url or '2.59.169.96' in base_url:
        result = _fetch_client_panel_data(base_url, username, password)
        if result is not None:
            return result
        return _fetch_running_monitor_data('SMS Hadi')
    # Number Panel — requires sesskey extraction from stats page
    if '51.89.99.105' in base_url:
        return _fetch_number_panel_data(base_url, username, password)
    # Purple SMS — special /dialer/ path with different column layout
    if '85.195.94.50' in base_url:
        return _fetch_purple_panel_data(base_url, username, password)
    # Wolf SMS — agent path, cookie-based auth, no captcha
    if '213.32.24.208' in base_url:
        result = _fetch_wolf_panel_data(base_url, username, password)
        if result is not None:
            return result
        return _fetch_running_monitor_data('Wolf sms')
    # Shark SMS — agent path, sesskey-based, math captcha + crlf
    if '65.109.111.158' in base_url:
        result = _fetch_shark_panel_data(base_url, username, password)
        if result is not None:
            return result
        return _fetch_running_monitor_data('Shark sms')
    # All other panels (SMS Hadi, Konekta, MSI, Proof, Lamix, Seven 1 Tel, Flex, Zento)
    # use /client/res/data_smscdr.php cookie-based auth — no sesskey needed
    return _fetch_client_panel_data(base_url, username, password)


def _fetch_running_monitor_data(panel_name: str) -> dict | None:
    _monitor_map = {
        'SMS Hadi':        monitor,
        'SMS Hadi 2':      sms_hadi2_monitor,
        'Konekta Premium': konekta_monitor,
        'Msi sms':         msi_sms_monitor,
        'Number Panel':    number_panel_monitor,
        'Purple sms':      purple_sms_monitor,
        'Proof sms':       proof_sms_monitor,
        'Lamix sms':       lamix_sms_monitor,
        'Seven 1 Tel':     seven1tel_monitor,
        'Flex sms':        mait_sms_monitor,
        'Zento sms':       zento_sms_monitor,
        'Wolf sms':        wolf_sms_monitor,
        'Shark sms':       shark_sms_monitor,
    }
    m = _monitor_map.get(panel_name)
    if m is None or not getattr(m, 'logged_in', False) or not getattr(m, 'session', None):
        return None
    try:
        raw_records = m._fetch_records()
        if raw_records is None:
            return None
        records = []
        for rec in raw_records:
            sms_body = rec.get('sms_body') or rec.get('message') or ''
            range_name = rec.get('range_name') or rec.get('country') or ''
            records.append({
                'datetime': rec.get('datetime') or rec.get('dt') or rec.get('received_at') or '',
                'country': _extract_country(range_name),
                'number': rec.get('number') or '',
                'otp': _extract_otp(sms_body),
                'website': rec.get('website') or _detect_website_from_body(sms_body),
                'message': sms_body,
            })
        return {'total': len(records), 'records': records}
    except Exception as exc:
        logger.error(f"fetch_running_monitor_data({panel_name}): Exception — {exc}")
        return None


def _fetch_client_panel_data(base_url: str, username: str, password: str) -> dict | None:
    """Fetch stats from a /client/SMSCDRStats panel (cookie-based auth)."""
    base_url   = base_url.rstrip('/')
    # Determine login page: Konekta uses /sign-in, others use /login
    if 'konektapremium' in base_url.lower():
        login_page = f"{base_url}/sign-in"
    else:
        login_page = f"{base_url}/login"
    signin_url = f"{base_url}/signin"
    ajax_url   = f"{base_url}/client/res/data_smscdr.php"
    referer    = f"{base_url}/client/SMSCDRStats"

    try:
        session = _new_session()

        r1 = session.get(login_page, timeout=15)
        captcha = _solve_captcha(r1.text)
        if not captcha:
            logger.error(f"fetch_client_panel_data ({base_url}): Could not solve captcha")
            return None

        r2 = session.post(
            signin_url,
            data={'username': username, 'password': password, 'capt': captcha},
            headers={'Referer': login_page},
            timeout=15, allow_redirects=True,
        )
        final_path = r2.url.lower()
        if 'login' in final_path or 'sign-in' in final_path:
            logger.error(f"fetch_client_panel_data ({base_url}): Login failed")
            return None

        url = _build_client_ajax_url(ajax_url, days_back=7)
        r3  = session.get(url, headers={
            'Referer': referer,
            'X-Requested-With': 'XMLHttpRequest',
        }, timeout=25)

        if 'login' in r3.url.lower() or 'sign-in' in r3.url.lower():
            logger.error(f"fetch_client_panel_data ({base_url}): Session expired during fetch")
            return None

        data  = r3.json()
        rows  = data.get('aaData', [])
        total = data.get('iTotalRecords', len(rows))

        records = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 5:
                continue
            dt_str     = str(row[0]).strip() if row[0] else ''
            range_name = str(row[1]).strip() if row[1] else ''
            number     = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
            sms_body   = str(row[4]).strip() if len(row) > 4 and row[4] else ''

            country = _extract_country(range_name)
            otp     = _extract_otp(sms_body)
            website = _detect_website_from_body(sms_body)

            records.append({
                'datetime': dt_str, 'country': country,
                'number': number, 'otp': otp,
                'website': website, 'message': sms_body,
            })

        return {'total': total, 'records': records}

    except Exception as exc:
        logger.error(f"fetch_client_panel_data ({base_url}): Exception — {exc}")
        return None


def _fetch_wolf_panel_data(base_url: str, username: str, password: str) -> dict | None:
    """Fetch stats from Wolf SMS panel (cookie-based, /agent/ path, no captcha)."""
    base_url   = base_url.rstrip('/')
    login_url  = f"{base_url}/login"
    signin_url = f"{base_url}/signin"
    ajax_url   = f"{base_url}/agent/res/data_smscdr.php"
    referer    = f"{base_url}/agent/SMSCDRStats"

    try:
        session = _new_session()
        # reCAPTCHA present on page but NOT enforced server-side — POST directly
        r2 = session.post(
            signin_url,
            data={'username': username, 'password': password},
            headers={'Referer': login_url},
            timeout=15, allow_redirects=True,
        )
        final_path = r2.url.lower()
        if 'login' in final_path or 'sign-in' in final_path:
            logger.error(f"_fetch_wolf_panel_data ({base_url}): Login failed")
            return None

        url = _build_client_ajax_url(ajax_url, days_back=7)
        r3  = session.get(url, headers={
            'Referer': referer,
            'X-Requested-With': 'XMLHttpRequest',
        }, timeout=25)
        if 'login' in r3.url.lower():
            logger.error(f"_fetch_wolf_panel_data ({base_url}): Session expired during fetch")
            return None

        data  = r3.json()
        rows  = data.get('aaData', [])
        total = data.get('iTotalRecords', len(rows))

        records = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 5:
                continue
            dt_str     = str(row[0]).strip() if row[0] else ''
            range_name = str(row[1]).strip() if row[1] else ''
            number     = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
            # Skip totals/summary rows — they don't have a valid datetime
            if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                continue
            sms_body   = str(row[4]).strip() if len(row) > 4 and row[4] else ''
            if not number or not dt_str:
                continue
            records.append({
                'datetime': dt_str,
                'country':  _extract_country(range_name),
                'number':   number,
                'otp':      _extract_otp(sms_body),
                'website':  _detect_website_from_body(sms_body),
                'message':  sms_body,
            })
        return {'total': total, 'records': records}

    except Exception as exc:
        logger.error(f"_fetch_wolf_panel_data ({base_url}): Exception — {exc}")
        return None


def _fetch_shark_panel_data(base_url: str, username: str, password: str) -> dict | None:
    """Fetch stats from Shark SMS panel (sesskey-based, /agent/ path, math captcha + crlf)."""
    base_url   = base_url.rstrip('/')
    login_url  = f"{base_url}/login"
    signin_url = f"{base_url}/signin"
    stats_url  = f"{base_url}/agent/SMSCDRStats"
    ajax_url   = f"{base_url}/agent/res/data_smscdr.php"

    try:
        session = _new_session()

        r1 = session.get(login_url, timeout=15)
        captcha = _solve_captcha(r1.text)
        if not captcha:
            logger.error(f"_fetch_shark_panel_data ({base_url}): Could not solve captcha")
            return None

        r2 = session.post(
            signin_url,
            data={'username': username, 'password': password, 'capt': captcha, 'crlf': ''},
            headers={'Referer': login_url},
            timeout=15, allow_redirects=True,
        )
        if 'login' in r2.url.lower():
            logger.error(f"_fetch_shark_panel_data ({base_url}): Login failed")
            return None

        r3 = session.get(stats_url, timeout=20)
        if 'login' in r3.url.lower():
            return None
        m = re.search(
            r'"sAjaxSource"\s*:\s*"res/data_smscdr\.php[^"]*sesskey=([^"&]+)"',
            r3.text
        )
        if not m:
            logger.error(f"_fetch_shark_panel_data ({base_url}): sesskey not found")
            return None
        sesskey = m.group(1)

        now = datetime.now()
        d1  = (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00')
        d2  = now.strftime('%Y-%m-%d 23:59:59')
        params = urlencode({
            'fdate1': d1, 'fdate2': d2,
            'frange': '', 'fnum': '', 'fcli': '',
            'fgdate': '', 'fgmonth': '', 'fgrange': '',
            'fgnumber': '', 'fgcli': '',
            'fg': '0',
            'sesskey': sesskey,
            'iDisplayStart': '0', 'iDisplayLength': '999999',
        })
        r4 = session.get(
            f"{ajax_url}?{params}",
            headers={'Referer': stats_url, 'X-Requested-With': 'XMLHttpRequest'},
            timeout=25,
        )
        if 'login' in r4.url.lower():
            return None

        data  = r4.json()
        rows  = data.get('aaData', [])
        total = data.get('iTotalRecords', len(rows))

        records = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 3:
                continue
            dt_str     = str(row[0]).strip() if row[0] else ''
            range_name = str(row[1]).strip() if row[1] else ''
            number     = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
            # Skip totals/summary rows — they don't have a valid datetime
            if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                continue
            sms_body   = ''
            if len(row) > 5:
                sms_body = str(row[5]).strip() if row[5] else ''
            elif len(row) > 4:
                sms_body = str(row[4]).strip() if row[4] else ''
            if not number or not dt_str:
                continue
            records.append({
                'datetime': dt_str,
                'country':  _extract_country(range_name),
                'number':   number,
                'otp':      _extract_otp(sms_body),
                'website':  _detect_website_from_body(sms_body),
                'message':  sms_body,
            })
        return {'total': total, 'records': records}

    except Exception as exc:
        logger.error(f"_fetch_shark_panel_data ({base_url}): Exception — {exc}")
        return None


def _fetch_number_panel_data(base_url: str, username: str, password: str) -> dict | None:
    """Fetch stats from Number Panel (sesskey-based, /client/ path)."""
    base_url    = base_url.rstrip('/')
    login_url   = f"{base_url}/login"
    signin_url  = f"{base_url}/signin"
    stats_url   = f"{base_url}/client/SMSCDRStats"
    ajax_url    = f"{base_url}/client/res/data_smscdr.php"

    try:
        session = _new_session()

        r1 = session.get(login_url, timeout=15)
        captcha = _solve_captcha(r1.text)
        if not captcha:
            logger.error("fetch_number_panel_data: Could not solve captcha")
            return None

        r2 = session.post(
            signin_url,
            data={'username': username, 'password': password, 'capt': captcha},
            headers={'Referer': login_url},
            timeout=15, allow_redirects=True,
        )
        if 'login' in r2.url.lower():
            logger.error("fetch_number_panel_data: Login failed")
            return None

        r3 = session.get(stats_url, timeout=20)
        if 'login' in r3.url.lower():
            return None
        m = re.search(
            r'"sAjaxSource"\s*:\s*"res/data_smscdr\.php[^"]*sesskey=([^"&]+)"',
            r3.text
        )
        if not m:
            logger.error("fetch_number_panel_data: sesskey not found")
            return None
        sesskey = m.group(1)

        now = datetime.now()
        d1  = (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00')
        d2  = now.strftime('%Y-%m-%d 23:59:59')
        params = urlencode({
            'fdate1': d1, 'fdate2': d2,
            'frange': '', 'fnum': '', 'fcli': '',
            'fgdate': '', 'fgmonth': '', 'fgrange': '',
            'fgnumber': '', 'fgcli': '',
            'fg': '0',
            'sesskey': sesskey,
            'iDisplayStart': '0', 'iDisplayLength': '999999',
            'iSortCol_0': '0', 'sSortDir_0': 'desc',
        })
        r4 = session.get(
            f"{ajax_url}?{params}",
            headers={'Referer': stats_url, 'X-Requested-With': 'XMLHttpRequest'},
            timeout=25,
        )
        if 'login' in r4.url.lower():
            return None

        data  = r4.json()
        rows  = data.get('aaData', [])
        total = data.get('iTotalRecords', len(rows))

        records = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 5:
                continue
            dt_str     = str(row[0]).strip() if row[0] else ''
            range_name = str(row[1]).strip() if row[1] else ''
            number     = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
            sms_body   = str(row[4]).strip() if len(row) > 4 and row[4] else ''
            if not number or number == '0':
                continue
            country = _extract_country(range_name)
            otp     = _extract_otp(sms_body)
            website = _detect_website_from_body(sms_body)
            records.append({
                'datetime': dt_str, 'country': country,
                'number': number, 'otp': otp,
                'website': website, 'message': sms_body,
            })

        return {'total': total, 'records': records}

    except Exception as exc:
        logger.error(f"fetch_number_panel_data: Exception — {exc}")
        return None


def _fetch_hadi_panel_data(base_url: str, username: str, password: str) -> dict | None:
    """Fetch stats from SMS Hadi panel (sesskey-based auth)."""
    login_url   = f"{base_url}/login"
    signin_url  = f"{base_url}/signin"
    reports_url = f"{base_url}/agent/SMSCDRReports"
    ajax_url    = f"{base_url}/agent/res/data_smscdr.php"

    try:
        session = _new_session()

        r1 = session.get(login_url, timeout=15)
        captcha = _solve_captcha(r1.text)
        if not captcha:
            logger.error("fetch_hadi_panel_data: Could not solve captcha")
            return None

        r2 = session.post(
            signin_url,
            data={'username': username, 'password': password, 'capt': captcha},
            headers={'Referer': login_url},
            timeout=15, allow_redirects=True,
        )
        if 'login' in r2.url.lower():
            logger.error("fetch_hadi_panel_data: Login failed")
            return None

        now       = datetime.now()
        yesterday = now - timedelta(days=1)
        d1        = yesterday.strftime('%Y-%m-%d 00:00:00')
        d2        = yesterday.strftime('%Y-%m-%d 23:59:59')

        r3 = session.post(
            reports_url,
            data={
                'fdate1': d1, 'fdate2': d2,
                'fnum': '', 'fcli': '', 'frange': '', 'fclient': '',
            },
            headers={'Referer': reports_url},
            timeout=20,
        )
        if 'login' in r3.url.lower():
            return None

        m = re.search(
            r'"sAjaxSource"\s*:\s*"res/data_smscdr\.php[^"]*sesskey=([^"&]+)"',
            r3.text
        )
        if not m:
            logger.error("fetch_hadi_panel_data: sesskey not found")
            return None
        sesskey = m.group(1)

        url = (ajax_url + '?' + urlencode({
            'fdate1': d1, 'fdate2': d2,
            'frange': '', 'fclient': '', 'fnum': '', 'fcli': '',
            'fgdate': '', 'fgmonth': '', 'fgrange': '', 'fgclient': '',
            'fgnumber': '', 'fgcli': '',
            'fg': '0', 'sesskey': sesskey,
            'iDisplayStart': '0', 'iDisplayLength': '999999',
            'iSortCol_0': '0', 'sSortDir_0': 'desc',
        }))

        r4 = session.get(url, headers={
            'Referer': reports_url,
            'X-Requested-With': 'XMLHttpRequest',
        }, timeout=25)

        if 'login' in r4.url.lower():
            return None

        data  = r4.json()
        rows  = data.get('aaData', [])
        total = data.get('iTotalRecords', len(rows))

        records = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            dt_str     = str(row[0]).strip() if row[0] else ''
            range_name = str(row[1]).strip() if row[1] else ''
            number     = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
            website    = str(row[3]).strip() if row[3] else 'Unknown'
            sms_body   = str(row[5]).strip() if len(row) > 5 and row[5] else ''
            detected = _detect_website_from_body(sms_body)
            if detected and detected != 'Unknown':
                website = detected

            country = _extract_country(range_name)
            otp     = _extract_otp(sms_body)

            records.append({
                'datetime': dt_str, 'country': country,
                'number': number, 'otp': otp,
                'website': website, 'message': sms_body,
            })

        return {'total': total, 'records': records}

    except Exception as exc:
        logger.error(f"fetch_hadi_panel_data: Exception — {exc}")
        return None


def _fetch_purple_panel_data(base_url: str, username: str, password: str) -> dict | None:
    """
    Fetch stats from Purple SMS panel.
    Cookie-based auth, AJAX at /dialer/ajax/dt_reports.php (no sesskey).
    Columns: [0]Date [1]Termination [2]Number [3]CLI [4]Currency
             [5]Payterm [6]Payout [7]Message
    """
    login_url  = f"{base_url}/SignIn"
    signin_url = f"{base_url}/signmein"
    stats_url  = f"{base_url}/dialer/SMSReports"
    ajax_url   = f"{base_url}/dialer/ajax/dt_reports.php"

    try:
        session = _new_session()

        r1 = session.get(login_url, timeout=15)
        captcha = _solve_captcha(r1.text)
        if not captcha:
            logger.error("fetch_purple_panel_data: Could not solve captcha")
            return None

        r2 = session.post(
            signin_url,
            data={'username': username, 'password': password, 'capt': captcha},
            headers={'Referer': login_url},
            timeout=15, allow_redirects=True,
        )
        final_lower = r2.url.lower().rstrip('/')
        final_last  = final_lower.split('/')[-1]
        if final_last in ('login', 'signin', 'sign-in') or final_last.endswith('signin'):
            logger.error("fetch_purple_panel_data: Login failed")
            return None

        now = datetime.now()
        d1  = (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00')
        d2  = now.strftime('%Y-%m-%d 23:59:59')
        params = {
            'fdate1': d1, 'fdate2': d2,
            'ftermination': '', 'fnum': '', 'fcli': '',
            'fgdate': '0', 'fgtermination': '0',
            'fgnumber': '0', 'fgcli': '0', 'fg': '0',
            'iDisplayStart': '0', 'iDisplayLength': '999999',
            'iSortCol_0': '0', 'sSortDir_0': 'desc',
        }
        r4 = session.get(
            ajax_url,
            params=params,
            headers={'Referer': stats_url, 'X-Requested-With': 'XMLHttpRequest'},
            timeout=25,
        )
        if 'login' in r4.url.lower() or 'signin' in r4.url.lower():
            return None

        data  = r4.json()
        rows  = data.get('aaData', [])
        total = data.get('iTotalRecords', len(rows))

        records = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 8:
                continue
            dt_str     = str(row[0]).strip() if row[0] else ''
            range_name = str(row[1]).strip() if row[1] else ''
            number     = re.sub(r'[^\d]', '', str(row[2])) if row[2] else ''
            # Skip totals/summary rows — they don't have a valid datetime
            if not re.match(r'\d{4}-\d{2}-\d{2}', dt_str):
                continue
            sms_body   = str(row[7]).strip() if row[7] else ''
            if not number or not dt_str:
                continue
            country = _extract_country(range_name)
            otp     = _extract_otp(sms_body)
            website = _detect_website_from_body(sms_body)
            records.append({
                'datetime': dt_str, 'country': country,
                'number': number, 'otp': otp,
                'website': website, 'message': sms_body,
            })

        return {'total': total, 'records': records}

    except Exception as exc:
        logger.error(f"fetch_purple_panel_data: Exception — {exc}")
        return None
