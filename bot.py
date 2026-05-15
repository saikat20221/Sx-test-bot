from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
import time

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                       ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from datetime import datetime, timedelta

from config import (
    BOT_TOKEN, OTP_GROUP_LINK,
    SUPPORT_GROUP_LINK, REQUIRED_CHANNELS,
    SMS_HADI_USERNAME, KONEKTA_USERNAME, MSI_SMS_USERNAME,
    NUMBER_PANEL_USERNAME, PURPLE_SMS_USERNAME, PROOF_SMS_USERNAME,
    LAMIX_SMS_USERNAME, SEVEN1TEL_USERNAME, MAIT_SMS_USERNAME,
    ZENTO_SMS_USERNAME, WOLF_SMS_USERNAME, SHARK_SMS_USERNAME,
    SMS_HADI2_USERNAME,
)
from database import (
    _init_db,
    _get_countries, _get_available_number_by_country,
    _assign_number_to_user,
    _get_numbers_count_by_country, _get_all_country_counts,
    _add_country, _add_numbers_to_country,
    _delete_number, _delete_all_numbers_from_country, _delete_country,
    _get_country_stats, _get_country_id_by_name,
    _reset_country_numbers, _reset_all_numbers,
    _add_admin, _add_admin_by_uid, _remove_admin, _get_all_admins,
    _get_all_admins_with_details, _is_admin,
    _add_user, _get_all_users, _get_all_users_with_info, _get_user_count,
    _get_user_stats_summary, _get_top_users_detailed,
    generate_users_excel, generate_user_stats_excel,
    _get_panels, _get_panel_by_name, _update_panel_credentials,
    _get_user_by_number,
    _get_latest_website_message, _get_latest_panel_message,
    _get_referral_settings, _set_referral_bonus, _toggle_referral,
    _get_user_balance, _update_user_balance, _set_user_balance,
    _credit_referral, _get_referral_count, _get_referral_total_earned,
    _get_user_referral_code, _get_user_by_ref_code, _get_user_info_by_id,
    _get_top_referrers,
    _get_min_withdraw, _set_min_withdraw,
    _create_withdraw_request, _get_pending_withdraws, _update_withdraw_status,
    _get_withdraw_request_by_id,
    _get_otp_bonus_settings, _toggle_otp_bonus, _set_otp_bonus_amount,
    _set_otp_daily_limit, _get_user_otp_bonus_stats,
    _reset_all_user_data, export_all_data_as_zip,
    _get_number_limit, _set_number_limit, _get_available_numbers_by_country,
    _get_all_panel_statuses, _update_panel_status,
    _set_panel_enabled, _is_panel_enabled,
    _get_country_otp_bonus, _set_country_otp_bonus,
    _reset_country_otp_bonus, _get_all_country_otp_bonuses,
    _get_country_services, _add_country_service,
    _delete_country_service, _get_all_services, _get_countries_by_service,
    _get_global_services, _add_global_service, _remove_global_service,
    _unmap_service_from_country,
    _add_extra_group, _remove_extra_group, _get_all_extra_groups,
    _get_panel_interval, _set_panel_interval,
    _get_panel_retry_interval, _set_panel_retry_interval,
    _get_setting, _set_setting,
    _get_bot_overview_stats,
    _get_required_channels, _add_required_channel,
    _update_required_channel, _delete_required_channel,
    _get_channel_check_interval, _set_channel_check_interval,
)
from keyboards import (
    get_admin_keyboard, get_admin_tools_keyboard, get_manage_numbers_keyboard,
    get_otp_bonus_keyboard, get_referral_keyboard,
    get_manage_admins_keyboard, get_user_keyboard, get_users_keyboard,
    get_settings_keyboard, get_edit_bot_links_keyboard,
    get_extra_groups_keyboard, get_channel_join_keyboard,
    country_number_keyboard, countries_inline_keyboard,
)


from otp_monitor import (
    monitor,
    konekta_monitor,
    msi_sms_monitor,
    zento_sms_monitor,
    number_panel_monitor,
    purple_sms_monitor,
    proof_sms_monitor,
    lamix_sms_monitor,
    seven1tel_monitor,
    mait_sms_monitor,
    wolf_sms_monitor,
    shark_sms_monitor,
    sms_hadi2_monitor,
    fetch_panel_data,
    get_panel_latest_today,
    _extract_all_otps,
    _notify_admins_login_success,
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Panel registry (ordered) ──────────────────────────────────────────────────
# Used by Panel Polling menu for index-based callback_data.
PANEL_LIST = [
    ('SMS Hadi',        monitor),
    ('Konekta Premium', konekta_monitor),
    ('Msi sms',         msi_sms_monitor),
    ('Number Panel',    number_panel_monitor),
    ('Purple sms',      purple_sms_monitor),
    ('Proof sms',       proof_sms_monitor),
    ('Lamix sms',       lamix_sms_monitor),
    ('Seven 1 Tel',     seven1tel_monitor),
    ('Flex sms',        mait_sms_monitor),
    ('Zento sms',       zento_sms_monitor),
    ('Wolf sms',        wolf_sms_monitor),
    ('Shark sms',       shark_sms_monitor),
]

# ── Multiple Panels section (second accounts / extra panels) ──────────────────
MULTIPLE_PANEL_LIST = [
    ('SMS Hadi 2', sms_hadi2_monitor),
]

# ── Combined list for operations that need ALL panels ─────────────────────────
ALL_PANEL_LIST = PANEL_LIST + MULTIPLE_PANEL_LIST

# ── Panel categorization ──────────────────────────────────────────────────────
PANEL_CATEGORY = {
    'SMS Hadi':        'client',
    'Konekta Premium': 'client',
    'Msi sms':         'client',
    'Number Panel':    'client',
    'Purple sms':      'client',
    'Proof sms':       'client',
    'Lamix sms':       'client',
    'Seven 1 Tel':     'client',
    'Flex sms':        'agent',
    'Zento sms':       'client',
    'Wolf sms':        'agent',
    'Shark sms':       'agent',
    'SMS Hadi 2':      'client',
}

# ── Panel config usernames (always from code/config, never from DB) ───────────
PANEL_CONFIG_USERNAMES: dict[str, str] = {
    'SMS Hadi':        SMS_HADI_USERNAME,
    'Konekta Premium': KONEKTA_USERNAME,
    'Msi sms':         MSI_SMS_USERNAME,
    'Number Panel':    NUMBER_PANEL_USERNAME,
    'Purple sms':      PURPLE_SMS_USERNAME,
    'Proof sms':       PROOF_SMS_USERNAME,
    'Lamix sms':       LAMIX_SMS_USERNAME,
    'Seven 1 Tel':     SEVEN1TEL_USERNAME,
    'Flex sms':        MAIT_SMS_USERNAME,
    'Zento sms':       ZENTO_SMS_USERNAME,
    'Wolf sms':        WOLF_SMS_USERNAME,
    'Shark sms':       SHARK_SMS_USERNAME,
    'SMS Hadi 2':      SMS_HADI2_USERNAME,
}


def _panels_in_category(category: str) -> list[str]:
    """Return the names of panels belonging to the given category, in
    PANEL_LIST order."""
    return [pname for pname, _m in PANEL_LIST
            if PANEL_CATEGORY.get(pname) == category]


def _md_escape(s: str) -> str:
    """Escape characters that have special meaning in Telegram legacy Markdown
    so panel names cannot accidentally break a message."""
    if not s:
        return ""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
        .replace("[", "\\[")
    )



async def _build_session_cleanup_view():
    """Build the Session Cleanup view — list every panel name in mono format,
    and ask admin to type the name they want to clean. No buttons."""
    statuses = await run_db(_get_all_panel_statuses)
    statuses_by_name = {s['panel_name']: s for s in (statuses or [])}

    lines = []
    for pname, _m in ALL_PANEL_LIST:
        s     = statuses_by_name.get(pname)
        is_en = await run_db(_is_panel_enabled, pname)
        if not is_en:
            icon = "🚫"
        elif s and s.get('logged_in'):
            icon = "✅"
        else:
            icon = "❌"
        lines.append(f"{icon} `{pname}`")

    msg = (
        "🧹 *Session Cleanup*\n\n"
        + "\n".join(lines)
        + "\n\n"
        "Type the *name* of the panel whose session you want to clean "
        "(copy it from the list above).\n\n"
        "_Send /cancel to cancel._"
    )
    return msg


async def _notify_admins_session_cleaned(bot, panel_name: str):
    """Notify all admins that a panel's session was cleaned. Bot will NOT
    auto-login the panel — admin must use ⏱ Retry Interval to re-login."""
    try:
        from database import _get_all_admins_with_details
        admins = _get_all_admins_with_details()
        text_msg = (
            f"🧹 *Session Cleanup Done*\n\n"
            f"🖥️ *{panel_name}* session, cookies and sesskey have been cleared.\n\n"
            f"⚠️ The bot will NOT log in automatically.\n"
            f"Use ⏱ *Retry Interval* to log in again."
        )
        for admin in admins:
            uid = admin.get("user_id")
            if uid:
                try:
                    await bot.send_message(chat_id=uid, text=text_msg, parse_mode="Markdown")
                except Exception:
                    pass
    except Exception:
        pass


async def _notify_admins_panel_toggled(bot, panel_name: str, enabled: bool):
    """Notify all admins that a panel was enabled or disabled."""
    try:
        from database import _get_all_admins_with_details
        admins = _get_all_admins_with_details()
        if enabled:
            text_msg = (
                f"🔌 *Panel Enabled*\n\n"
                f"✅ *{panel_name}* is now enabled. The bot will start monitoring this panel."
            )
        else:
            text_msg = (
                f"🔌 *Panel Disabled*\n\n"
                f"🚫 *{panel_name}* is now disabled. The bot has stopped monitoring this panel."
            )
        for admin in admins:
            uid = admin.get("user_id")
            if uid:
                try:
                    await bot.send_message(chat_id=uid, text=text_msg, parse_mode="Markdown")
                except Exception:
                    pass
    except Exception:
        pass


async def _build_extra_groups_overview(context) -> str:
    """List every Extra Group's name and chat ID in mono format inside a
    single message. Used by the 📢 Extra Groups button overview."""
    groups = await run_db(_get_all_extra_groups)
    if not groups:
        return (
            "📢 *Extra Groups*\n\n"
            "No extra groups have been added yet.\n\n"
            "Select an option from the keyboard below."
        )
    lines = []
    for g in groups:
        lines.append(f"`{g['title']}`\n`{g['chat_id']}`")
    msg = (
        f"📢 *Extra Groups* (Total: *{len(groups)}*)\n\n"
        + "\n\n".join(lines)
        + "\n\nSelect an option from the keyboard below."
    )
    if len(msg) > 4000:
        msg = msg[:3990] + "\n…"
    return msg


async def _build_panel_toggle_view():
    """Build the 🔌 Panel Toggle screen — a single message listing all panels
    in mono format (so admin can copy-paste the name) with their current
    enabled/disabled state. Admin types the panel name to toggle it."""
    lines = []
    enabled_count = 0
    for pname, _m in ALL_PANEL_LIST:
        en = bool(await run_db(_is_panel_enabled, pname))
        if en:
            enabled_count += 1
            lines.append(f"✅ `{pname}`")
        else:
            lines.append(f"🚫 `{pname}`")
    total = len(lines)
    msg = (
        "🔌 *Panel Toggle*\n\n"
        f"Total: *{total}*  |  ✅ Enabled: *{enabled_count}*  |  "
        f"🚫 Disabled: *{total - enabled_count}*\n\n"
        + "\n".join(lines)
        + "\n\n"
        "Copy the *name* of the panel you want to enable/disable and send it.\n"
        "(Currently Enabled → will be Disabled; Disabled → will be Enabled.)\n\n"
        "_Send /cancel to cancel._"
    )
    return msg


async def _build_retry_login_view():
    """Build the ⏱ Retry Interval screen — show ONLY the panels that failed
    to log in (in `mono` format). Admin types a panel name to manually
    trigger that panel's login. On success, all admins are notified."""
    statuses = await run_db(_get_all_panel_statuses)
    statuses_by_name = {s['panel_name']: s for s in (statuses or [])}

    failed_lines = []
    for pname, _m in ALL_PANEL_LIST:
        s     = statuses_by_name.get(pname)
        is_en = await run_db(_is_panel_enabled, pname)
        if not is_en:
            continue
        if s and s.get('logged_in'):
            continue
        failed_lines.append(f"❌ `{pname}`")

    if not failed_lines:
        msg = (
            "⏱ *Retry Interval*\n\n"
            "🎉 All enabled panels are now logged in successfully.\n"
            "No failed panels — nothing to retry."
        )
        return msg, False

    msg = (
        "⏱ *Retry Interval — Failed Panels*\n\n"
        + "\n".join(failed_lines)
        + "\n\n"
        "Copy the *name* of the panel you want to retry login for "
        "(from the list above) and send it.\n\n"
        "All admins will be notified when login succeeds.\n\n"
        "_Send /cancel to cancel._"
    )
    return msg, True


# ── Message Queue (Telegram rate-limit safe sender) ───────────────────────────
# Queues outgoing messages and sends them at max 25/sec with auto-retry.
# Usage: await enqueue_message(bot, chat_id, text, **kwargs)

_msg_queue: asyncio.Queue | None = None   # created lazily in post_init (correct event loop)
_MSG_RATE   = 30          # max messages per second (Telegram hard limit is 30)
_MSG_RETRY  = 5           # number of retries on failure
_MSG_DELAY  = 1.0 / _MSG_RATE   # minimum delay between sends


async def _message_queue_worker():
    """Background coroutine: drains _msg_queue and sends at a safe rate."""
    while True:
        try:
            item = await _msg_queue.get()
            if item is None:
                _msg_queue.task_done()
                break
            bot, chat_id, text, kwargs = item
            for attempt in range(1, _MSG_RETRY + 1):
                try:
                    await bot.send_message(chat_id=chat_id, text=text, **kwargs)
                    break
                except Exception as exc:
                    err_str = str(exc).lower()
                    if 'flood' in err_str or 'too many' in err_str:
                        wait = 5 * attempt
                        logger.warning(f"[MsgQueue] FloodWait → sleeping {wait}s")
                        await asyncio.sleep(wait)
                    elif attempt < _MSG_RETRY:
                        await asyncio.sleep(1.0 * attempt)
                    else:
                        logger.error(f"[MsgQueue] Failed to send to {chat_id}: {exc}")
            await asyncio.sleep(_MSG_DELAY)
            _msg_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[MsgQueue] Worker error: {e}")


async def enqueue_message(bot, chat_id: int, text: str, **kwargs):
    """Put a message into the send queue (non-blocking)."""
    if _msg_queue is not None:
        await _msg_queue.put((bot, chat_id, text, kwargs))


# ── Memory Cleanup ─────────────────────────────────────────────────────────────
# Runs every 30 minutes to purge stale entries from in-memory dicts,
# preventing unbounded RAM growth when the bot runs for days.

_CLEANUP_INTERVAL = 1800   # 30 minutes
_SEMAPHORE_TTL    = 3600   # remove semaphores idle > 1 hour
_semaphore_last_used: dict[int, float] = {}   # user_id -> last access timestamp


async def _memory_cleanup_loop():
    """Periodic background task: clean up stale in-memory state."""
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL)
        try:
            now = time.monotonic()

            # Clean _user_semaphores ── remove entries idle for > TTL
            stale_sems = [
                uid for uid, last in _semaphore_last_used.items()
                if (now - last) > _SEMAPHORE_TTL
            ]
            for uid in stale_sems:
                _user_semaphores.pop(uid, None)
                _semaphore_last_used.pop(uid, None)

            # Clean _cn_timestamps ── remove entries older than 60 s
            stale_ts = [uid for uid, ts_list in _cn_timestamps.items()
                        if not ts_list or (now - max(ts_list)) > 60]
            for uid in stale_ts:
                _cn_timestamps.pop(uid, None)

            # Clean _cn_cooldown ── remove expired cooldowns
            stale_cd = [uid for uid, until in _cn_cooldown.items() if now > until]
            for uid in stale_cd:
                _cn_cooldown.pop(uid, None)

            # Clean _membership_cache (imported lazily)
            try:
                from bot import _membership_cache  # noqa: F401 (same module)
                cutoff = now - 120
                expired = [k for k, v in list(_membership_cache.items())
                           if v.get('ts', 0) < cutoff]
                for k in expired:
                    _membership_cache.pop(k, None)
            except Exception:
                pass

            cleaned = len(stale_sems) + len(stale_ts) + len(stale_cd)
            if cleaned:
                logger.info(f"[MemCleanup] Removed {cleaned} stale memory entries "
                            f"| queue_size={_msg_queue.qsize()}")
        except Exception as e:
            logger.error(f"[MemCleanup] Error: {e}")


# ── Change Number rate limiter ─────────────────────────────────────────────────
# Tracks recent press timestamps and active cooldowns per user
_cn_timestamps: dict[int, list[float]] = {}   # user_id -> list of recent press times
_cn_cooldown:   dict[int, float]       = {}   # user_id -> cooldown_until (unix timestamp)

_CN_WINDOW   = 1.0   # seconds — if pressed ≥2 times within this window → cooldown
_CN_MAX_HITS = 2     # max presses allowed inside the window before cooldown
_CN_COOLDOWN = 3.0   # seconds to wait after triggering the limit

# ── Panel emoji mapping ────────────────────────────────────────────────────────

_PANEL_EMOJIS = {
    'SMS Hadi':        '📡',
    'Konekta Premium': '👑',
    'Msi sms':         '📲',
    'Number Panel':    '🔢',
    'Purple sms':      '💜',
    'Proof sms':       '✅',
    'Lamix sms':       '🌐',
    'Seven 1 Tel':     '📱',
    'Flex sms':        '💬',
    'Zento sms':       '🟢',
    'SMS Hadi 2':      '📡',
}

def _panel_label(name: str) -> str:
    """Return emoji + panel name for display on keyboard buttons."""
    emoji = _PANEL_EMOJIS.get(name, '🖥️')
    return f"{emoji} {name}"

def _panel_name_from_label(label: str) -> str:
    """Strip leading emoji/status prefix from a panel button label to get the raw panel name.

    Handles all cases:
      '📡 SMS Hadi 2'  → 'SMS Hadi 2'
      '✅ Wolf sms'    → 'Wolf sms'
      '🚫 Shark sms'  → 'Shark sms'
      '💬 Flex sms'   → 'Flex sms'
      'SMS Hadi 2'    → 'SMS Hadi 2'   (no prefix, returned as-is)
    """
    label = label.strip('`').strip()
    # Strip known status prefixes first
    for prefix in ("✅ ", "🚫 ", "✅", "🚫"):
        if label.startswith(prefix):
            label = label[len(prefix):].strip()
            break
    # Strip any remaining leading emoji (non-ASCII, non-alphanumeric first token)
    parts = label.split(' ', 1)
    if len(parts) == 2:
        first = parts[0]
        # If the first token has no ASCII letter or digit it is an emoji — strip it
        if not any(c.isascii() and (c.isalpha() or c.isdigit()) for c in first):
            return parts[1].strip()
    return label


def _resolve_panel_user(panel_dict: dict, pname: str) -> str:
    """Return a clean display username for a panel.

    Rejects the stored value if it is empty, matches the panel name, or
    accidentally matches the keyboard button label (emoji + name) — all of
    which indicate the username was never set or was corrupted.
    """
    stored = (panel_dict.get('username') or '').strip()
    # Treat stored value as invalid when it equals the panel name or label
    if stored in ('', pname, _panel_label(pname)):
        stored = ''
    # Fall back to the hard-coded config username, then to '—'
    fallback = (PANEL_CONFIG_USERNAMES.get(pname) or '').strip()
    if fallback in ('', pname, _panel_label(pname)):
        fallback = ''
    return stored or fallback or '—'

# Global dict: chat_id → asyncio.Task for "Latest Message" auto-refresh
_refresh_tasks: dict[int, asyncio.Task] = {}

# ── Per-user concurrency limiter ──────────────────────────────────────────────
# Each user gets at most 3 concurrent handler coroutines so a single user
# cannot flood the bot with thousands of rapid-fire requests.
_user_semaphores: dict[int, asyncio.Semaphore] = {}


def _get_user_sem(user_id: int) -> asyncio.Semaphore:
    if user_id not in _user_semaphores:
        _user_semaphores[user_id] = asyncio.Semaphore(3)
    _semaphore_last_used[user_id] = time.monotonic()
    return _user_semaphores[user_id]


def run_db(func, *args, **kwargs):
    return asyncio.to_thread(func, *args, **kwargs)


# ── Auto user tracker ─────────────────────────────────────────────────────────
# Records every user who interacts with the bot — even if they never typed
# /start. This makes Broadcast and Force Start reach all users who clicked
# any user-panel button or sent a message, and the admin User Count reflects
# the true number of users.

async def _ensure_user_tracked(update: Update) -> None:
    """Idempotently add the user to our DB on ANY interaction. Safe to call
    on every update — _add_user only inserts new rows or refreshes name fields
    for existing rows."""
    try:
        u = update.effective_user if update else None
        if not u or u.is_bot:
            return
        # Only track 1:1 chats with the bot — not group/channel users
        chat = update.effective_chat
        if chat and chat.type and chat.type != "private":
            return
        await run_db(_add_user, u.id, u.username, u.first_name, u.last_name, None)
    except Exception as e:
        # Never let tracking break a real handler
        logger.warning(f"[AutoTrack] failed: {e}")


# ── Powerful bulk-send engine (for Broadcast and Force Start) ────────────────
# Concurrent sender with categorised result reporting and live progress.

async def _bulk_send(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    title: str,
    user_ids: list,
    sender,
    concurrency: int = 25,
    max_attempts: int = 3,
    retry_blocked: bool = False,
):
    """Concurrently call `sender(uid)` for every uid in user_ids and report
    progress / categorised stats live. `sender` is an async callable that
    raises on failure and returns on success.

    max_attempts:    total attempts per user before giving up
    retry_blocked:   if True, also re-attempt users who returned Forbidden
                     (useful for Force Start so a freshly un-blocked user
                     receives /start on a subsequent retry inside the loop)"""
    from telegram.error import (
        Forbidden, BadRequest, RetryAfter, TimedOut, NetworkError,
    )

    total = len(user_ids)
    sem   = asyncio.Semaphore(max(1, concurrency))
    stats = {
        "sent": 0, "blocked": 0, "deactivated": 0,
        "not_found": 0, "other": 0, "done": 0,
    }
    lock = asyncio.Lock()

    status_msg = await update.message.reply_text(
        f"{title}\n\n📊 Total: *{total}* users\n⏳ Starting broadcast…",
        parse_mode='Markdown',
    )

    async def _progress_updater():
        while True:
            await asyncio.sleep(2)
            async with lock:
                done = stats["done"]
                snapshot = dict(stats)
            try:
                await status_msg.edit_text(
                    f"{title}\n\n"
                    f"📊 Total: *{total}*\n"
                    f"📤 Sent: *{done}* / {total}\n"
                    f"✅ Success: *{snapshot['sent']}*\n"
                    f"🚫 Blocked: *{snapshot['blocked']}*\n"
                    f"💤 Deactivated: *{snapshot['deactivated']}*\n"
                    f"❓ Chat not found: *{snapshot['not_found']}*\n"
                    f"⚠️ Other errors: *{snapshot['other']}*",
                    parse_mode='Markdown',
                )
            except Exception:
                pass
            if done >= total:
                return

    async def _send_one(uid):
        async with sem:
            blocked_so_far = False
            sent_ok = False
            for attempt in range(max_attempts):
                try:
                    await sender(uid)
                    async with lock:
                        stats["sent"] += 1
                        if blocked_so_far:
                            # Adjust: this user was previously counted blocked
                            # in this same loop — but we recovered. Keep stats
                            # accurate.
                            pass
                    sent_ok = True
                    break
                except RetryAfter as e:
                    await asyncio.sleep(getattr(e, "retry_after", 1) + 0.5)
                except (TimedOut, NetworkError):
                    await asyncio.sleep(1.0 + attempt)
                except Forbidden:
                    blocked_so_far = True
                    if retry_blocked and attempt < max_attempts - 1:
                        # Wait a bit and try again — user may un-block
                        await asyncio.sleep(0.7 + attempt * 0.3)
                        continue
                    async with lock:
                        stats["blocked"] += 1
                    break
                except BadRequest as e:
                    text_e = str(e).lower()
                    async with lock:
                        if "deactivated" in text_e:
                            stats["deactivated"] += 1
                        elif "chat not found" in text_e or "user not found" in text_e:
                            stats["not_found"] += 1
                        else:
                            stats["other"] += 1
                    break
                except Exception:
                    async with lock:
                        stats["other"] += 1
                    break
            async with lock:
                stats["done"] += 1

    progress_task = asyncio.create_task(_progress_updater())
    await asyncio.gather(*[_send_one(uid) for uid in user_ids])
    try:
        await asyncio.wait_for(progress_task, timeout=3)
    except Exception:
        progress_task.cancel()

    reach_pct = (stats["sent"] / total * 100) if total else 0.0
    try:
        await status_msg.edit_text(
            f"{title} — *Done!* ✅\n\n"
            f"📊 Total users: *{total}*\n"
            f"✅ Success: *{stats['sent']}*  ({reach_pct:.1f}%)\n"
            f"🚫 Blocked: *{stats['blocked']}*\n"
            f"💤 Deactivated: *{stats['deactivated']}*\n"
            f"❓ Chat not found: *{stats['not_found']}*\n"
            f"⚠️ Other errors: *{stats['other']}*",
            parse_mode='Markdown',
        )
    except Exception:
        pass
    return stats


async def _run_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Powerful broadcast: supports two modes.

    1. Forward Mode — if the admin forwards a message to the bot, it is
       forwarded (with the original "Forwarded from …" header visible) to
       every tracked user via bot.forward_message().

    2. Copy Mode (existing behaviour) — if the admin types / sends their own
       message, it is copied (no forward header) to every tracked user via
       bot.copy_message().

    Both modes support any content type: text, photo, video, document, voice,
    sticker, etc.  Concurrent dispatch with categorised result reporting."""
    user_ids = await run_db(_get_all_users)
    src_chat = update.effective_chat.id
    src_msg  = update.message.message_id

    # Detect whether the incoming message is a forwarded one.
    # PTB v20+ exposes forward_origin; older fields forward_from /
    # forward_from_chat are kept as fallback.
    msg = update.message
    is_forwarded = bool(
        getattr(msg, 'forward_origin', None)
        or getattr(msg, 'forward_from', None)
        or getattr(msg, 'forward_from_chat', None)
        or getattr(msg, 'forward_sender_name', None)
    )

    if is_forwarded:
        broadcast_title = "📢 *Broadcast Running… (Forward Mode)*"

        async def _sender(uid):
            await context.bot.forward_message(
                chat_id=uid,
                from_chat_id=src_chat,
                message_id=src_msg,
            )
    else:
        broadcast_title = "📢 *Broadcast Running…*"

        async def _sender(uid):
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=src_chat,
                message_id=src_msg,
            )

    await _bulk_send(
        update, context,
        title=broadcast_title,
        user_ids=user_ids,
        sender=_sender,
        concurrency=25,
    )


async def _run_force_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Powerful Force Start — forces a fresh /start for every tracked user.

    Behaviour:
      • Sends the exact same /start welcome + user reply-keyboard that the
        bot would send if the user themselves typed /start. Nothing else —
        no extra text, no inline buttons.
      • Targets EVERY user in the DB, including users who have only ever
        clicked a user-panel button (auto-tracked) and users who have never
        used /start themselves.
      • Even attempts users who previously blocked the bot — with extra
        retries — so the moment a user un-blocks, the next attempt succeeds.
      • Concurrent dispatch with categorised live progress and final report.

    Telegram-imposed limit (cannot be bypassed by any bot, anywhere):
      A bot can ONLY message users that have at least one prior chat with
      the bot. Users who have never opened a chat with this bot at all are
      unreachable until they themselves tap /start once. Blocked users stay
      Forbidden until they un-block. We attempt and report both honestly."""
    # Build the exact /start view that show_main_menu sends
    start_text = (
        "🤖 *Welcome to Number Bot!*\n\n"
        "Stay with us, I hope you can learn something good. "
        "Join the live regularly. "
        "Join all my channels and groups.\n\n"
        "🧑‍💻 *Bot Owner:* ADMIN LIMON\n\n"
        "*Available Options:*\n"
        "☎️ Get Number      — Get phone numbers by country\n"
        "🌍 Available Country — View available numbers statistics\n\n"
        "Choose an option below:"
    )
    user_kb = get_user_keyboard()

    user_ids = await run_db(_get_all_users)

    async def _sender(uid):
        # Make sure the user is in our DB (idempotent — same as the real /start)
        try:
            await run_db(_add_user, uid, None, None, None, None)
        except Exception:
            pass
        # Send only the /start welcome with the user reply keyboard — no
        # inline buttons, no extra messages, no links.
        await context.bot.send_message(
            chat_id=uid,
            text=start_text,
            parse_mode='Markdown',
            reply_markup=user_kb,
            disable_web_page_preview=True,
        )

    await _bulk_send(
        update, context,
        title="🚀 *Force Start Running…*",
        user_ids=user_ids,
        sender=_sender,
        concurrency=25,
        # Extra retry attempts — gives blocked users that just un-blocked a
        # better chance to receive the /start instantly.
        max_attempts=5,
        retry_blocked=True,
    )


# ── Latest Message helpers ─────────────────────────────────────────────────────

def _safe_inline(s: str) -> str:
    """Remove backticks from a value so it is safe inside single-backtick inline code."""
    return str(s).replace('`', "'")


def _format_panel_latest(rec: dict, pname: str = "") -> str:
    header   = f"🖥️ *{_md_escape(pname)} — Latest Message*\n\n" if pname else "📨 *Latest Message from SMS CDR Stats*\n\n"
    msg_body = rec.get('message') or '—'
    all_otps = _extract_all_otps(msg_body) if msg_body != '—' else (rec.get('otp') or '—')
    dt_val   = _safe_inline(rec.get('datetime') or rec.get('msg_timestamp') or rec.get('received_at') or '—')
    country  = _safe_inline(rec.get('country') or '—')
    number   = _safe_inline(rec.get('number') or '—')
    website  = _safe_inline(rec.get('website') or rec.get('website_name') or '—')
    otp_val  = _safe_inline(all_otps)
    return (
        f"{header}"
        f"📅 Date/Time: `{dt_val}`\n"
        f"🌍 Country: `{country}`\n"
        f"📲 Number: `+{number}`\n"
        f"🌐 Service: `{website}`\n"
        f"🔐 OTP: `{otp_val}`\n\n"
        f"💬 Full Message :\n```\n{msg_body}\n```"
    )


_STOP_MARKUP = InlineKeyboardMarkup([[
    InlineKeyboardButton("⏹ Stop Auto-Refresh", callback_data="stop_refresh"),
]])


async def _refresh_latest_msg_loop(
    chat_id: int,
    message_id: int,
    bot,
    last_rec_id: int,
    pname: str = "",
):
    """
    Background task: every 3 seconds fetch the latest record from the DB
    (populated by OTPMonitor which polls the SMS CDR Stats page every 3 s)
    and edit the displayed message. User notifications are handled by OTPMonitor.
    """
    current_last_id = last_rec_id

    while True:
        try:
            await asyncio.sleep(3)

            if pname:
                # Auto-refresh uses in-memory cache (force_live=False) —
                # the background monitor already polls every 3 s, so cache
                # is always fresh without hitting the panel twice.
                rec = await asyncio.to_thread(get_panel_latest_today, pname)
            else:
                rec = None
            if not rec:
                continue

            if rec.get('id') == current_last_id:
                continue

            current_last_id = rec.get('id')

            text = _format_panel_latest(rec, pname=pname)
            if len(text) > 4000:
                text = text[:3990] + "\n…"

            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode='Markdown',
                    reply_markup=_STOP_MARKUP,
                )
            except Exception:
                try:
                    plain = text.replace('`', "'").replace('*', '').replace('_', '').replace('[', '(')
                    if len(plain) > 4000:
                        plain = plain[:3990] + "\n…"
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=plain,
                        reply_markup=_STOP_MARKUP,
                    )
                except Exception:
                    pass

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"_refresh_latest_msg_loop error: {exc}")
            await asyncio.sleep(3)


# ── Channel Membership Check ──────────────────────────────────────────────────

# Cache: user_id -> (is_member: bool, expires_at: float)
_membership_cache: dict[int, tuple[bool, float]] = {}
_MEMBERSHIP_CACHE_TTL       = 300.0   # member cache: recheck after 5 min
_MEMBERSHIP_CACHE_TTL_FAIL  = 30.0    # non-member cache: recheck after 30 s (fast verify)

# ── Periodic membership enforcer ───────────────────────────────────────────────
_notified_recently: dict[int, float] = {}   # user_id → last notified timestamp
_NOTIFY_COOLDOWN   = 120.0                  # 2 minutes between re-notifications per user
_channel_admin_warned: set = set()          # channels already warned about missing admin rights


async def check_membership(bot, user_id: int) -> bool:
    """
    Check if user is a member of all required channels (loaded live from DB).
    Result is cached per user for _MEMBERSHIP_CACHE_TTL seconds.
    Bot must be admin of each channel for this to work reliably.
    """
    from telegram.error import BadRequest, Forbidden, TelegramError

    channels = await run_db(_get_required_channels)
    if not channels:
        return True

    now = asyncio.get_running_loop().time()
    cached = _membership_cache.get(user_id)
    if cached is not None:
        is_member, expires_at = cached
        if now < expires_at:
            return is_member

    async def _check_one(ch: dict) -> bool:
        ch_id = ch['id']
        try:
            member = await bot.get_chat_member(chat_id=ch_id, user_id=user_id)
            status = member.status
            logger.info(f"[MemberCheck] user={user_id} ch={ch_id} status={status}")
            return status not in ('left', 'kicked', 'restricted')
        except BadRequest as e:
            err = str(e).lower()
            # Any error that clearly means "user is not a member" → block
            if any(x in err for x in (
                'user not found', 'not found', 'participant', 'not a member',
                'member list is inaccessible', 'chat_admin_required',
                'need to be invited', 'bot is not a member',
            )):
                if 'inaccessible' in err or 'admin_required' in err:
                    if ch_id not in _channel_admin_warned:
                        _channel_admin_warned.add(ch_id)
                        logger.warning(
                            f"[MemberCheck] ch={ch_id} — Bot is NOT admin! "
                            "Add bot as Admin to the channel for membership checks to work."
                        )
                return False
            logger.warning(f"[MemberCheck] ch={ch_id} BadRequest: {e}")
            return False  # fail closed — channels are mandatory
        except Forbidden as e:
            logger.warning(
                f"[MemberCheck] ch={ch_id} Forbidden: {e} — Bot not in channel! Add bot as Admin."
            )
            return False  # fail closed — cannot verify = treat as not member
        except TelegramError as e:
            logger.warning(f"[MemberCheck] ch={ch_id} TelegramError: {e}")
            return False  # fail closed
        except Exception as e:
            logger.warning(f"[MemberCheck] ch={ch_id} unexpected error: {e}")
            return False  # fail closed

    results = await asyncio.gather(*[_check_one(ch) for ch in channels])
    is_member = all(results)

    ttl = _MEMBERSHIP_CACHE_TTL if is_member else _MEMBERSHIP_CACHE_TTL_FAIL
    _membership_cache[user_id] = (is_member, now + ttl)
    return is_member


async def send_join_channels_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the Access Locked message with join buttons."""
    channels = await run_db(_get_required_channels)
    keyboard = [
        [InlineKeyboardButton(f"📢 {ch['name']} — Join Now", url=ch['url'])]
        for ch in channels
    ]
    keyboard.append([InlineKeyboardButton("✅ Verify — I Joined All", callback_data="check_join")])
    markup = InlineKeyboardMarkup(keyboard)
    ch_count = len(channels)
    text = (
        "🔒 *বট ব্যবহার করতে চ্যানেলে জয়েন করুন!*\n\n"
        f"নিচের *{ch_count}টি চ্যানেলে* জয়েন করা বাধ্যতামূলক।\n"
        "জয়েন না করলে বট ব্যবহার করা যাবে না।\n\n"
        "👇 সব চ্যানেলে জয়েন করুন, তারপর *Verify* চাপুন:"
    )
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode='Markdown', reply_markup=markup
            )
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text, parse_mode='Markdown', reply_markup=markup
            )
    else:
        await update.message.reply_text(
            text, parse_mode='Markdown', reply_markup=markup
        )


async def _require_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Returns True if user may proceed, False if they were shown the join-channels message.
    Admins always pass through.
    """
    user_id  = update.effective_user.id
    username = update.effective_user.username
    if _is_admin(username, user_id):
        return True
    ok = await check_membership(context.bot, user_id)
    if not ok:
        await send_join_channels_message(update, context)
        return False
    return True


# ── Show helpers ──────────────────────────────────────────────────────────────

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *Welcome to Number Bot!*\n\n"
        "Stay with us, I hope you can learn something good. "
        "Join the live regularly. "
        "Join all my channels and groups.\n\n"
        "🧑‍💻 *Bot Owner:* ADMIN LIMON\n\n"
        "*Available Options:*\n"
        "☎️ Get Number      — Get phone numbers by country\n"
        "🌍 Available Country — View available numbers statistics\n\n"
        "Choose an option below:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=msg, parse_mode='Markdown',
            reply_markup=get_user_keyboard(),
        )
    else:
        await update.message.reply_text(msg, parse_mode='Markdown',
                                        reply_markup=get_user_keyboard())


async def _build_service_manager_text() -> str:
    """Build the Service Manager overview message for admins."""
    services  = await run_db(_get_global_services)
    countries = await run_db(_get_countries)
    counts    = await run_db(_get_all_country_counts)
    bonuses   = await run_db(_get_all_country_otp_bonuses)
    global_cfg   = await run_db(_get_otp_bonus_settings)
    global_bonus = global_cfg.get('amount', 0.0)

    svc_lines = "\n".join(f"• `{s}`" for s in services) if services else "_No services yet._"

    country_lines = []
    for cid, cname in countries:
        _total, avail = counts.get(cid, (0, 0))
        bonus = bonuses.get(cid, global_bonus)
        flag = ""
        try:
            from otp_monitor import _detect_iso_from_number, country_code_to_flag
        except Exception:
            pass
        country_lines.append(f"🌍 `{cname}` ({avail}) - 💰 {bonus:.2f}")
    country_text = "\n".join(country_lines) if country_lines else "_No countries yet._"

    return (
        "🔧 *Service Manager*\n\n"
        f"*Current Services:*\n{svc_lines}\n\n"
        f"🌍 *Available Countries:*\n{country_text}\n\n"
        "➕ To add a new service, just type the name (e.g., Netflix)\n"
        "🗑️ To remove a service, type: `delete Viber`\n\n"
        "🗺️ *Service x Country*\n"
        "To map a country to a service, type:\n"
        "`map ServiceName CountryName`\n"
        "Example: `map WhatsApp Myanmar FB`\n\n"
        "To unmap:\n"
        "`unmap ServiceName CountryName`\n\n"
        "Send /cancel to exit."
    )


async def show_countries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ── If services are configured, show service selection first ─────────────
    services = await run_db(_get_global_services)
    if services:
        rows = []
        for svc in services:
            safe = svc[:50]
            rows.append([InlineKeyboardButton(f"{svc}", callback_data=f"svc_pick_{safe}")])
        markup = InlineKeyboardMarkup(rows)
        txt = "📋 *Select a Service*\n\nChoose the service you need a number for:"
        if update.callback_query:
            await update.callback_query.edit_message_text(
                txt, reply_markup=markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(
                txt, reply_markup=markup, parse_mode='Markdown')
        return

    # ── No services configured — show all countries directly ─────────────────
    countries = await run_db(_get_countries)
    if not countries:
        txt = "❌ No countries available at the moment."
        if update.callback_query:
            await update.callback_query.edit_message_text(txt)
        else:
            await update.message.reply_text(txt)
        return

    counts = await run_db(_get_all_country_counts)
    data = [(row[0], row[1], counts.get(row[0], (0, 0))[1]) for row in countries]

    markup = countries_inline_keyboard(data)
    if not markup:
        txt = "❌ No numbers available at the moment."
        if update.callback_query:
            await update.callback_query.edit_message_text(txt)
        else:
            await update.message.reply_text(txt)
        return

    txt = "*Available Countries*\n\nSelect a country to get numbers:"
    if update.callback_query:
        await update.callback_query.edit_message_text(
            txt, reply_markup=markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(
            txt, reply_markup=markup, parse_mode='Markdown')


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    countries  = await run_db(_get_countries)
    counts     = await run_db(_get_all_country_counts)
    bonuses    = await run_db(_get_all_country_otp_bonuses)
    global_cfg = await run_db(_get_otp_bonus_settings)
    global_bonus = global_cfg.get('amount', 0.0)

    active = [(cid, cname) for cid, cname in countries if counts.get(cid, (0, 0))[0] > 0]

    if not active:
        msg = "*No countries available yet.*"
    else:
        lines = [
            f"*Total Countries: {len(active)}*",
            f"*{'─' * 20}*",
        ]
        for cid, cname in active:
            total, avail = counts.get(cid, (0, 0))
            bonus_val = bonuses.get(cid, global_bonus)
            lines.append(
                f"*Country : {cname}*\n"
                f"*Numbers : {avail}*\n"
                f"*OTP Bonus: {bonus_val:.2f} ৳ BDT*"
            )
            lines.append(f"*{'─' * 20}*")
        msg = "\n".join(lines)

    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, parse_mode='Markdown')


# ── /cancel ───────────────────────────────────────────────────────────────────

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    username = update.effective_user.username
    user_id  = update.effective_user.id


    # Clear all other awaiting states and return to appropriate menu
    _awaiting_keys = [
        'awaiting_country_name', 'awaiting_numbers_file', 'awaiting_new_country_name',
        'awaiting_add_numbers_country', 'awaiting_reset_country_name',
        'awaiting_delete_country_name', 'awaiting_specific_number_delete',
        'awaiting_new_admin',
        'awaiting_ref_bonus', 'awaiting_min_withdraw',
        'awaiting_otp_bonus_amount',
        'awaiting_balance_user_id', 'awaiting_balance_amount', 'balance_edit_target_id',
        'awaiting_withdraw_method', 'awaiting_withdraw_account', 'awaiting_withdraw_amount',
        'awaiting_number_limit',
        'withdraw_method', 'withdraw_account', 'withdraw_amount',
        'awaiting_reset_users_confirm',
        'awaiting_panel_interval',
        'awaiting_panel_retry',
        'awaiting_session_cleanup_panel',
        'awaiting_retry_login_panel',
        'awaiting_reload_interval_panel',
        'awaiting_reload_interval_seconds',
        'awaiting_cred_panel',
        'awaiting_cred_username',
        'panel_toggle_active',
        'panel_list_active', 'panel_list_multiple_active', 'panel_view_active',
        'panel_list_source', 'panel_category_active', 'panel_list_category',
        'current_country_name', 'delete_target_country_id', 'delete_target_country_name',
        'edit_country_id', 'edit_country_name',
        'service_manager_active',
    ]
    cleared = any(context.user_data.get(k) for k in _awaiting_keys)
    for k in _awaiting_keys:
        context.user_data.pop(k, None)

    if cleared:
        msg = "❌ *Operation cancelled.*"
    else:
        msg = "ℹ️ No operation was in progress to cancel."

    markup = get_admin_keyboard() if _is_admin(username, user_id) else get_user_keyboard()
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=markup)


# ── Admin start ───────────────────────────────────────────────────────────────

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    username = update.effective_user.username
    user_id  = update.effective_user.id
    if not _is_admin(username, user_id):
        await update.message.reply_text("❌ You are not authorized to use admin commands.")
        return

    await update.message.reply_text(
        "🛠️ *Admin Panel*\n\nWelcome! Use the buttons below.",
        parse_mode='Markdown',
        reply_markup=get_admin_keyboard()
    )


# ── /start ────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    user     = update.effective_user
    username = user.username
    user_id  = user.id

    # Check referral code from deep link: /start ref_XXXXXXXX
    referred_by_id = None
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            token = arg[4:]
            referrer_id = None
            if token.isdigit():
                referrer_id = int(token)
            else:
                referrer_id = await run_db(_get_user_by_ref_code, token)
            if referrer_id and referrer_id != user_id:
                referred_by_id = referrer_id

    await run_db(_add_user, user.id, user.username, user.first_name, user.last_name, referred_by_id)

    # ── Channel membership gate ────────────────────────────────────────────────
    if not _is_admin(username, user_id):
        ok = await check_membership(context.bot, user_id)
        if not ok:
            await send_join_channels_message(update, context)
            return

    # Credit referral bonus if applicable
    if referred_by_id:
        settings = await run_db(_get_referral_settings)
        if settings['enabled']:
            credited = await run_db(_credit_referral, referred_by_id, user_id, settings['bonus'])
            if credited:
                try:
                    referrer_info = await run_db(_get_user_info_by_id, referred_by_id)
                    name = referrer_info['first_name'] if referrer_info else "friend"
                    await context.bot.send_message(
                        chat_id=referred_by_id,
                        text=(
                            f"🎉 *Referral Bonus Received!*\n\n"
                            f"A new user joined via your referral link.\n"
                            f"💰 Bonus added: *৳ {settings['bonus']:.2f}*\n\n"
                            f"Press '💰 My Balance' to see your total balance."
                        ),
                        parse_mode='Markdown',
                    )
                except Exception:
                    pass

    welcome = (
        "🤖 *Welcome to Number Bot!*\n\n"
        "Stay with us, I hope you can learn something good. "
        "Join the live regularly. "
        "Join all my channels and groups.\n\n"
        "🧑‍💻 *Bot Owner:* ADMIN LIMON"
    )
    reply_kb = get_admin_keyboard() if _is_admin(username, user_id) else get_user_keyboard()
    await update.message.reply_text(welcome, parse_mode='Markdown')
    await update.message.reply_text("📋 Use the menu below:", reply_markup=reply_kb)


# ── Callback handler ──────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    # Auto-track every interaction so users who never typed /start are still
    # counted in the admin User Count and reachable by Broadcast/Force Start.
    await _ensure_user_tracked(update)
    query    = update.callback_query
    data     = query.data
    user_id  = query.from_user.id
    username = query.from_user.username

    # ── Copy OTP toast ─────────────────────────────────────────────────────────
    if data.startswith("copy_otp:"):
        otp_val = data[len("copy_otp:"):]
        await query.answer(text=otp_val, show_alert=False)
        return

    # ── Channel verify button ──────────────────────────────────────────────────
    if data == "check_join":
        # Force a fresh check by clearing the cache for this user
        _membership_cache.pop(user_id, None)
        ok = await check_membership(context.bot, user_id)
        if not ok:
            await query.answer("❌ You haven't joined all channels yet!", show_alert=True)
            return
        await query.answer()
        # Membership confirmed — show welcome
        welcome = (
            "✅ *Verified! Welcome!*\n\n"
            "🤖 *Welcome to Number Bot!*\n\n"
            "Stay with us, I hope you can learn something good. "
            "Join the live regularly. "
            "Join all my channels and groups.\n\n"
            "🧑‍💻 *Bot Owner:* ADMIN LIMON"
        )
        reply_kb = get_admin_keyboard() if _is_admin(username, user_id) else get_user_keyboard()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=welcome, parse_mode='Markdown'
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📋 Use the menu below:",
            reply_markup=reply_kb
        )
        try:
            await query.delete_message()
        except Exception:
            pass
        return

    await query.answer()

    # ── Top Users Refresh ──────────────────────────────────────────────────────
    if data == "top_users_refresh":
        from datetime import timezone, timedelta as _td
        _tz_bd = timezone(_td(hours=6))
        now_str = datetime.now(_tz_bd).strftime("%d %b %Y, %I:%M %p")
        top5 = await run_db(_get_top_users_detailed, 5)
        lines = ["*🏆 Top 5 Users*", ""]
        for i, u in enumerate(top5, 1):
            name = u['display_name'] or f"ID:{u['user_id']}"
            bal  = u.get('balance', 0.0)
            lines.append(f"`{'─'*28}`")
            lines.append(f"`🏅 #{i}  {name}`")
            lines.append(f"`💎 Uid        : {u['user_id']}`")
            lines.append(f"`📨 OTP Msgs   : {u['msgs_received']}`")
            lines.append(f"`💰 Balance    : {bal:.2f} ৳`")
            lines.append("")
        lines.append(f"`{'─'*28}`")
        lines.append(f"`🕐 {now_str} (UTC+6)`")
        refresh_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="top_users_refresh")]
        ])
        try:
            await query.edit_message_text(
                "\n".join(lines),
                parse_mode='Markdown',
                reply_markup=refresh_markup,
            )
        except Exception:
            pass
        return

    # ── Membership gate for all other callbacks ────────────────────────────────
    if data != "stop_refresh":
        if not _is_admin(username, user_id):
            ok = await check_membership(context.bot, user_id)
            if not ok:
                await send_join_channels_message(update, context)
                return

    # ── Navigation ────────────────────────────────────────────────────────────
    if data == "get_numbers":
        await show_countries(update, context)
        return

    # ── Service pick (user flow) ──────────────────────────────────────────────
    if data.startswith("svc_pick_"):
        service_name = data[len("svc_pick_"):]
        countries = await run_db(_get_countries_by_service, service_name)
        counts    = await run_db(_get_all_country_counts)
        data_rows = [(cid, cname, counts.get(cid, (0, 0))[1]) for cid, cname in countries]
        markup    = countries_inline_keyboard(data_rows)
        if not markup:
            await query.edit_message_text(
                f"❌ No numbers available for *{service_name}* right now.",
                parse_mode='Markdown',
            )
            return
        back_row  = [[InlineKeyboardButton("🔙 Back to Services", callback_data="get_numbers")]]
        final_kb  = InlineKeyboardMarkup(list(markup.inline_keyboard) + back_row)
        await query.edit_message_text(
            f"🔧 *{service_name}*\n\nSelect a country to get your number:",
            reply_markup=final_kb,
            parse_mode='Markdown',
        )
        return

    if data == "view_stats":
        await show_stats(update, context)
        return

    if data == "back_to_main":
        await show_main_menu(update, context)
        return

    # ── Get a number for a country ────────────────────────────────────────────
    if data.startswith("country_") or data.startswith("another_"):
        prefix     = "country_" if data.startswith("country_") else "another_"
        country_id = int(data[len(prefix):])

        # Rate limit only for "Change Number" (another_) button
        if data.startswith("another_"):
            now = time.time()
            # Check if user is currently in cooldown
            if _cn_cooldown.get(user_id, 0) > now:
                remaining = int(_cn_cooldown[user_id] - now) + 1
                await query.answer(
                    f"⏳ Slow down! Wait {remaining} second(s).",
                    show_alert=True
                )
                return
            # Record this press and remove timestamps outside the window
            presses = _cn_timestamps.get(user_id, [])
            presses = [t for t in presses if now - t < _CN_WINDOW]
            presses.append(now)
            _cn_timestamps[user_id] = presses
            # If hit limit, start cooldown
            if len(presses) >= _CN_MAX_HITS:
                _cn_cooldown[user_id] = now + _CN_COOLDOWN
                _cn_timestamps[user_id] = []
                await query.answer(
                    f"⏳ Slow down! Wait {int(_CN_COOLDOWN)} second(s).",
                    show_alert=True
                )
                return

        countries    = await run_db(_get_countries)
        country_name = next((r[1] for r in countries if r[0] == country_id), "Unknown")

        limit   = await run_db(_get_number_limit)
        numbers = await run_db(_get_available_numbers_by_country, country_id, limit)

        if not numbers:
            await query.edit_message_text("❌ No numbers available for this country.")
            return

        for num in numbers:
            await run_db(_assign_number_to_user, user_id, num, country_id)

        otp_link = await run_db(_get_setting, "bot_link_getotp", OTP_GROUP_LINK)
        markup = country_number_keyboard(country_id, otp_link, numbers=numbers)

        await query.edit_message_text(
            f"🌍 *{country_name}*\n\n"
            f"Click a number button below to copy it:\n\n"
            "⏳ Waiting for OTP...",
            reply_markup=markup,
            parse_mode='Markdown',
        )
        return

    # ── User Withdraw callbacks (available to all users) ──────────────────────
    if data.startswith("wd_method_"):
        method_map = {
            "wd_method_binance": "Binance",
            "wd_method_bkash":   "bKash",
            "wd_method_nagad":   "Nagad",
        }
        method = method_map.get(data, "Unknown")
        context.user_data['awaiting_withdraw_method'] = False
        context.user_data['withdraw_method']          = method
        context.user_data['awaiting_withdraw_account']= True
        await query.edit_message_text(
            f"📱 *Enter your {method} number/account*\n\n"
            f"Enter your {method} number or account number:",
            parse_mode='Markdown'
        )
        return

    if data == "wd_cancel":
        context.user_data.pop('awaiting_withdraw_method',  None)
        context.user_data.pop('awaiting_withdraw_account', None)
        context.user_data.pop('awaiting_withdraw_amount',  None)
        context.user_data.pop('withdraw_method',           None)
        context.user_data.pop('withdraw_account',          None)
        context.user_data.pop('withdraw_amount',           None)
        await query.edit_message_text("❌ Withdraw cancelled.")
        return

    if data == "wd_confirm":
        amount  = context.user_data.get('withdraw_amount', 0)
        method  = context.user_data.get('withdraw_method', '')
        account = context.user_data.get('withdraw_account', '')
        if not (amount and method and account):
            await query.answer("❌ Incomplete information.", show_alert=True)
            return
        balance = await run_db(_get_user_balance, user_id)
        if balance < amount:
            await query.edit_message_text("❌ Insufficient balance! Withdraw cancelled.")
            return
        await run_db(_update_user_balance, user_id, -amount)
        req_id = await run_db(_create_withdraw_request, user_id, amount, method, account)
        for key in ('withdraw_amount', 'withdraw_method', 'withdraw_account',
                    'awaiting_withdraw_amount', 'awaiting_withdraw_account'):
            context.user_data.pop(key, None)
        await query.edit_message_text(
            f"✅ *Withdraw Request Submitted Successfully!*\n\n"
            f"🔢 Request ID: `#{req_id}`\n"
            f"💰 Amount: *৳ {amount:.2f}*\n"
            f"📱 Method: *{method}*\n"
            f"📞 Account: `{account}`\n\n"
            f"An admin will verify and send the funds. Thank you! 🙏",
            parse_mode='Markdown'
        )
        all_users = await run_db(_get_all_users_with_info)
        user_info = await run_db(_get_user_info_by_id, user_id)
        name = f"@{user_info['username']}" if user_info and user_info['username'] else str(user_id)
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve_{req_id}"),
             InlineKeyboardButton("❌ Reject",  callback_data=f"wd_reject_{req_id}")],
        ])
        notif_text = (
            f"💸 *New Withdraw Request!*\n\n"
            f"👤 User: *{name}* (`{user_id}`)\n"
            f"💰 Amount: *৳ {amount:.2f}*\n"
            f"📱 Method: *{method}*\n"
            f"📞 Account: `{account}`"
        )
        for uid, uname, *_ in all_users:
            if _is_admin(uname, uid):
                try:
                    await context.bot.send_message(
                        chat_id=uid, text=notif_text,
                        parse_mode='Markdown', reply_markup=markup)
                except Exception:
                    pass
        return

    # ── Admin-only callbacks ──────────────────────────────────────────────────
    if not _is_admin(username, user_id):
        await query.answer("❌ You are not authorized.", show_alert=True)
        return

    # delete_country_completely_<id>
    if data.startswith("delete_country_completely_"):
        cid = int(data.split("_")[-1])
        countries    = await run_db(_get_countries)
        cname        = next((r[1] for r in countries if r[0] == cid), "Unknown")
        nd, cd       = await run_db(_delete_country, cid)
        back_markup  = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Delete Menu", callback_data="back_to_delete"),
        ]])
        if cd:
            await query.edit_message_text(
                f"✅ *'{cname}' country and {nd} number(s) deleted!*",
                parse_mode='Markdown', reply_markup=back_markup)
        else:
            await query.edit_message_text(
                f"❌ Failed to delete '{cname}'!",
                reply_markup=back_markup)
        return

    # delete_all_<id>
    if data.startswith("delete_all_"):
        cid      = int(data.split("_")[-1])
        countries = await run_db(_get_countries)
        cname     = next((r[1] for r in countries if r[0] == cid), "Unknown")
        deleted   = await run_db(_delete_all_numbers_from_country, cid)
        await query.edit_message_text(
            f"✅ *{deleted} number(s) deleted from {cname}!*",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Delete Menu", callback_data="back_to_delete"),
            ]]))
        return

    # delete_country_<id>  (show options menu)
    if data.startswith("delete_country_"):
        cid      = int(data.split("_")[-1])
        countries = await run_db(_get_countries)
        cname     = next((r[1] for r in countries if r[0] == cid), "Unknown")
        total, avail = await run_db(_get_numbers_count_by_country, cid)
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🗑️ Delete all numbers ({total})",
                                  callback_data=f"delete_all_{cid}")],
            [InlineKeyboardButton("🔥 Delete entire country",
                                  callback_data=f"delete_country_completely_{cid}")],
            [InlineKeyboardButton("✏️ Delete specific number",
                                  callback_data=f"delete_specific_{cid}")],
            [InlineKeyboardButton("🔙 Back",
                                  callback_data="back_to_delete")],
        ])
        await query.edit_message_text(
            f"*{cname}*\n\nTotal: {total} numbers | Available: {avail}\n\n"
            "⚠️ *Warning:* This will be permanently deleted!",
            reply_markup=markup, parse_mode='Markdown')
        return

    # admin_info_<username> — show admin details + confirm remove button
    if data.startswith("admin_info_"):
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        aname   = data[len("admin_info_"):]
        admins  = await run_db(_get_all_admins_with_details)
        adm     = next((a for a in admins if a['username'] == aname), None)
        if not adm:
            await query.answer("❌ Admin not found.", show_alert=True)
            return
        fname     = adm.get('first_name') or '—'
        lname     = adm.get('last_name')  or ''
        full_name = f"{fname} {lname}".strip()
        uid_val   = adm.get('user_id')
        added_at  = adm.get('added_at') or '—'
        msg = (
            f"👤 *Admin Details*\n\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 🏷️ Name: *{full_name}*\n"
            f"┃ 🆔 UID: `{uid_val or '—'}`\n"
            f"┃ 📅 Admin since: `{added_at}`\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Remove this admin?"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ Yes, Remove", callback_data=f"confirm_remove_admin_{aname}"),
             InlineKeyboardButton("❌ Cancel", callback_data="back_to_admin")],
        ])
        await query.edit_message_text(msg, parse_mode='Markdown', reply_markup=keyboard)
        return

    # confirm_remove_admin_<username>
    if data.startswith("confirm_remove_admin_"):
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        admin_name = data[len("confirm_remove_admin_"):]
        ok, msg    = await run_db(_remove_admin, admin_name)
        if ok:
            await query.edit_message_text(
                f"✅ *Admin Removed Successfully!*\n\nAdmin no longer exists.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="back_to_admin")
                ]]))
        else:
            await query.edit_message_text(
                f"❌ {msg}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="back_to_admin")
                ]]))
        return

    # remove_admin_<username> (legacy — kept for safety)
    if data.startswith("remove_admin_"):
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        admin_name = data[len("remove_admin_"):]
        ok, msg    = await run_db(_remove_admin, admin_name)
        if ok:
            await query.edit_message_text(
                f"✅ Admin @{admin_name} removed successfully!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="back_to_admin")
                ]]))
        else:
            await query.edit_message_text(
                f"❌ {msg}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="back_to_admin")
                ]]))
        return

    # protected_admin_<username>
    if data.startswith("protected_admin_"):
        pname = data[len("protected_admin_"):]
        await query.answer(f"🛡️ {pname} is a protected admin and cannot be removed!",
                           show_alert=True)
        return

    # back_to_delete
    if data == "back_to_delete":
        countries = await run_db(_get_countries)
        if not countries:
            await query.edit_message_text("❌ No countries available.")
            return
        counts = await run_db(_get_all_country_counts)
        keyboard = []
        for row in countries:
            cid, cname = row[0], row[1]
            total, _   = counts.get(cid, (0, 0))
            keyboard.append([InlineKeyboardButton(
                f"🗑️ {cname} ({total})", callback_data=f"delete_country_{cid}")])
        keyboard.append([InlineKeyboardButton(
            "🔙 Back to Admin Panel", callback_data="back_to_admin")])
        await query.edit_message_text(
            "*Delete Numbers/Countries*\n\nSelect a country:",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return

    # back_to_admin
    if data == "back_to_admin":
        await query.answer()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="*🛠️ Admin Panel*",
            parse_mode='Markdown',
            reply_markup=get_admin_keyboard(),
        )
        return

    # ── Referral callbacks ─────────────────────────────────────────────────────

    if data == "ref_toggle":
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        settings = await run_db(_get_referral_settings)
        new_state = not settings['enabled']
        await run_db(_toggle_referral, new_state)
        status = "✅ Active" if new_state else "❌ Inactive"
        settings2 = await run_db(_get_referral_settings)
        min_wd2   = await run_db(_get_min_withdraw)
        pending2  = await run_db(_get_pending_withdraws)
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Enable" if not new_state else "❌ Disable",
                callback_data="ref_toggle"
            )],
            [InlineKeyboardButton("💰 Change Bonus Amount",      callback_data="ref_set_bonus")],
            [InlineKeyboardButton("📤 Set Minimum Withdraw Amount", callback_data="ref_set_min_withdraw")],
            [InlineKeyboardButton("👤 Edit User Balance",   callback_data="ref_edit_balance")],
            [InlineKeyboardButton(f"💸 Pending Withdraws ({len(pending2)})", callback_data="ref_pending_withdraws")],
        ])
        await query.edit_message_text(
            f"🎁 *Referral Settings Updated!*\n\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 🔘 Status: *{status}*\n"
            f"┃ 💰 Bonus per referral: *৳ {settings2['bonus']:.2f}*\n"
            f"┃ 📤 Min Withdraw: *৳ {min_wd2:.2f}*\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━",
            parse_mode='Markdown',
            reply_markup=markup
        )
        return

    if data == "ref_set_bonus":
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        context.user_data['awaiting_ref_bonus'] = True
        await query.edit_message_text(
            "💰 *Change Bonus Amount*\n\n"
            "Enter how much bonus to give per referral:\n"
            "_(Example: 10 or 25.50)_",
            parse_mode='Markdown'
        )
        return

    if data == "ref_edit_balance":
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        context.user_data['awaiting_balance_user_id'] = True
        await query.edit_message_text(
            "👤 *Edit User Balance*\n\n"
            "Enter the *Telegram User ID* of the user whose balance you want to edit:",
            parse_mode='Markdown'
        )
        return

    if data == "ref_set_min_withdraw":
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        context.user_data['awaiting_min_withdraw'] = True
        await query.edit_message_text(
            "📤 *Set Minimum Withdraw Amount*\n\n"
            "Enter the minimum amount a user can withdraw:\n"
            "_(Example: 50 or 100)_",
            parse_mode='Markdown'
        )
        return

    if data == "ref_pending_withdraws":
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        pending = await run_db(_get_pending_withdraws)
        if not pending:
            await query.answer("✅ No pending withdrawals.", show_alert=True)
            return
        for req in pending[:5]:
            name = f"@{req['username']}" if req['username'] else req['first_name'] or str(req['user_id'])
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve_{req['id']}"),
                 InlineKeyboardButton("❌ Reject",  callback_data=f"wd_reject_{req['id']}")],
            ])
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    f"💸 *Withdraw Request #{req['id']}*\n\n"
                    f"👤 User: *{name}* (`{req['user_id']}`)\n"
                    f"💰 Amount: *৳ {req['amount']:.2f}*\n"
                    f"📱 Method: *{req['method']}*\n"
                    f"📞 Account: `{req['account']}`\n"
                    f"🕐 Time: {req['created_at']}"
                ),
                parse_mode='Markdown',
                reply_markup=markup
            )
        await query.answer()
        return


    if data.startswith("wd_approve_"):
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        req_id = int(data.split("_")[-1])
        req = await run_db(_get_withdraw_request_by_id, req_id)
        await run_db(_update_withdraw_status, req_id, 'approved')
        await query.edit_message_text(
            f"✅ *Withdraw #{req_id} Approved!*\n\nApproved by admin.",
            parse_mode='Markdown'
        )
        if req:
            try:
                await context.bot.send_message(
                    chat_id=req['user_id'],
                    text=(
                        f"✅ *Your Withdraw Request #{req_id} has been approved!*\n\n"
                        f"💰 Amount: *৳ {req['amount']:.2f}*\n"
                        f"📱 Method: *{req['method']}*\n"
                        f"📞 Account: `{req['account']}`\n\n"
                        f"An admin will send the funds to your account soon. Thank you! 🙏"
                    ),
                    parse_mode='Markdown'
                )
            except Exception:
                pass
        return

    if data.startswith("wd_reject_"):
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        req_id = int(data.split("_")[-1])
        req = await run_db(_get_withdraw_request_by_id, req_id)
        await run_db(_update_withdraw_status, req_id, 'rejected')
        if req and req['status'] == 'pending':
            await run_db(_update_user_balance, req['user_id'], req['amount'])
            try:
                await context.bot.send_message(
                    chat_id=req['user_id'],
                    text=(
                        f"❌ *Your Withdraw Request #{req_id} has been rejected!*\n\n"
                        f"💰 ৳ {req['amount']:.2f} has been refunded to your balance."
                    ),
                    parse_mode='Markdown'
                )
            except Exception:
                pass
        await query.edit_message_text(
            f"❌ *Withdraw #{req_id} Rejected!*\n\nRejected by admin. User's balance has been refunded.",
            parse_mode='Markdown'
        )
        return

    # ── OTP Bonus callbacks ────────────────────────────────────────────────────

    if data == "otp_bonus_toggle":
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        settings  = await run_db(_get_otp_bonus_settings)
        new_state = not settings['enabled']
        await run_db(_toggle_otp_bonus, new_state)
        settings2 = await run_db(_get_otp_bonus_settings)
        status    = "✅ Active" if new_state else "❌ Inactive"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Enable" if not new_state else "❌ Disable",
                callback_data="otp_bonus_toggle"
            )],
            [InlineKeyboardButton("💰 Set Bonus Amount per OTP", callback_data="otp_bonus_set_amount")],
            [InlineKeyboardButton("👤 Edit User Balance",     callback_data="ref_edit_balance")],
        ])
        await query.edit_message_text(
            f"🎯 *OTP Bonus Settings Updated!*\n\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 🔘 Status: *{status}*\n"
            f"┃ 💰 Bonus per OTP: *৳ {settings2['amount']:.2f}*\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━",
            parse_mode='Markdown',
            reply_markup=markup
        )
        return

    if data == "otp_bonus_set_amount":
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        context.user_data['awaiting_otp_bonus_amount'] = True
        await query.edit_message_text(
            "💰 *Set OTP Bonus Amount*\n\n"
            "Enter how much bonus a user receives per OTP notification:\n"
            "_(Example: 2 or 5.50)_",
            parse_mode='Markdown'
        )
        return

    # reset_country_<id>
    if data.startswith("reset_country_"):
        cid      = int(data.split("_")[-1])
        countries = await run_db(_get_countries)
        cname     = next((r[1] for r in countries if r[0] == cid), "Unknown")
        reset     = await run_db(_reset_country_numbers, cid)
        total, av = await run_db(_get_numbers_count_by_country, cid)
        await query.edit_message_text(
            f"✅ *Reset Successful!*\n\n"
            f"🌍 Country: *{cname}*\n"
            f"🔄 Reset: *{reset}* number(s)\n"
            f"📊 Now Available: *{av}/{total}*\n\n"
            "All used numbers are available again.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Back to Reset Menu", callback_data="back_to_reset"),
            ]]))
        return

    # reset_all_countries
    if data == "reset_all_countries":
        total_reset = await run_db(_reset_all_numbers)
        await query.edit_message_text(
            f"✅ *Full Reset Successful!*\n\n"
            f"🔄 Total Reset: *{total_reset}* number(s) across all countries\n\n"
            "All used numbers are available again.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Back to Reset Menu", callback_data="back_to_reset"),
            ]]))
        return

    # back_to_reset — re-show reset countries menu
    if data == "back_to_reset":
        countries = await run_db(_get_countries)
        if not countries:
            await query.edit_message_text("❌ No countries available.")
            return
        counts = await run_db(_get_all_country_counts)
        keyboard = []
        for row in countries:
            cid, cname   = row[0], row[1]
            total, avail = counts.get(cid, (0, 0))
            used         = total - avail
            keyboard.append([InlineKeyboardButton(
                f"🔄 {cname} (Used: {used}/{total})",
                callback_data=f"reset_country_{cid}")])
        keyboard.append([InlineKeyboardButton(
            "🔄 Reset ALL Countries", callback_data="reset_all_countries")])
        keyboard.append([InlineKeyboardButton(
            "🔙 Back to Admin Panel", callback_data="back_to_admin")])
        await query.edit_message_text(
            "*🔄 𝑹𝒆𝒔𝒆𝒕 𝑵𝒖𝒎𝒃𝒆𝒓*\n\nSelect a country to reset:",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return

    # cob_sel_<id> — country OTP bonus: select country
    if data.startswith("cob_sel_"):
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        cid        = int(data[len("cob_sel_"):])
        countries  = await run_db(_get_countries)
        counts     = await run_db(_get_all_country_counts)
        cname      = next((r[1] for r in countries if r[0] == cid), "Unknown")
        total, _   = counts.get(cid, (0, 0))
        current    = await run_db(_get_country_otp_bonus, cid)
        global_cfg = await run_db(_get_otp_bonus_settings)
        if current is not None:
            status_line = f"🎯 Custom Bonus: *৳ {current:.2f}*"
        else:
            status_line = f"🌐 Global Default: *৳ {global_cfg['amount']:.2f}* _(no custom set)_"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Set Bonus Amount", callback_data=f"cob_set_{cid}")],
            [InlineKeyboardButton("🔄 Reset to Global Default",  callback_data=f"cob_rst_{cid}")],
            [InlineKeyboardButton("🔙 Back to Country List",   callback_data="cob_list")],
        ])
        await query.edit_message_text(
            f"🌍 *Country OTP Bonus*\n\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 🗺️ Country: `{cname}`\n"
            f"┃ 🔢 Total Numbers: *{total}*\n"
            f"┃ {status_line}\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Select what you want to do:",
            parse_mode='Markdown',
            reply_markup=markup
        )
        return

    # cob_set_<id> — set bonus amount for country
    if data.startswith("cob_set_"):
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        cid   = int(data[len("cob_set_"):])
        countries = await run_db(_get_countries)
        cname = next((r[1] for r in countries if r[0] == cid), "Unknown")
        context.user_data['awaiting_country_otp_bonus'] = cid
        context.user_data['awaiting_country_otp_name']  = cname
        await query.edit_message_text(
            f"✏️ *`{cname}`* — Set OTP Bonus\n\n"
            f"How much bonus to give when an OTP is received on this country's number?\n"
            f"_(Example: 3 or 5.50)_\n\n"
            f"Send /cancel to cancel.",
            parse_mode='Markdown'
        )
        return

    # cob_rst_<id> — reset country bonus to global default
    if data.startswith("cob_rst_"):
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        cid  = int(data[len("cob_rst_"):])
        countries = await run_db(_get_countries)
        cname = next((r[1] for r in countries if r[0] == cid), "Unknown")
        await run_db(_reset_country_otp_bonus, cid)
        global_cfg = await run_db(_get_otp_bonus_settings)
        await query.edit_message_text(
            f"✅ *`{cname}`* — Bonus Reset!\n\n"
            f"Global Default will now be used.\n"
            f"🌐 Global Default: *৳ {global_cfg['amount']:.2f}*",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Country List", callback_data="cob_list")
            ]])
        )
        return

    # cob_list — re-show country OTP bonus list
    if data == "cob_list":
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        try:
            countries  = await run_db(_get_countries)
            counts     = await run_db(_get_all_country_counts)
            bonuses    = await run_db(_get_all_country_otp_bonuses)
            global_cfg = await run_db(_get_otp_bonus_settings)
            keyboard   = []
            lines      = []
            for row in countries:
                cid, cname  = row[0], row[1]
                total, _    = counts.get(cid, (0, 0))
                if total == 0:
                    continue
                custom = bonuses.get(cid)
                bonus_str = f"৳{custom:.2f}" if custom is not None else "default"
                keyboard.append([InlineKeyboardButton(
                    f"🌍 {cname} ({total} numbers) — {bonus_str}",
                    callback_data=f"cob_sel_{cid}"
                )])
                if custom is not None:
                    lines.append(f"  `{cname}` ({total}): ৳ {custom:.2f} (custom)")
                else:
                    lines.append(f"  `{cname}` ({total}): ৳ {global_cfg['amount']:.2f} (default)")
            if not keyboard:
                await query.edit_message_text(
                    "❌ No numbers have been added to any country yet.",
                    parse_mode='Markdown'
                )
                return
            keyboard.append([InlineKeyboardButton("🔙 Close", callback_data="cob_close")])
            summary = "\n".join(lines) if lines else "(no settings)"
            msg = (
                f"🌍 *Country OTP Bonus Settings*\n\n"
                f"┣━━━━━━━━━━━━━━━━━━━━━\n"
                f"┃ 🌐 Global Default: *৳ {global_cfg['amount']:.2f}*\n"
                f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{summary}\n\n"
                f"Select a country:"
            )
            await query.edit_message_text(
                msg,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"cob_list callback error: {e}", exc_info=True)
            await query.answer(f"❌ Load failed: {e}", show_alert=True)
        return

    # cob_close
    if data == "cob_close":
        await query.edit_message_text("✅ Country OTP Bonus menu closed.")
        return

    # edit_numbers
    if data == "edit_numbers":
        countries = await run_db(_get_countries)
        if not countries:
            await query.edit_message_text("❌ No countries available.")
            return
        counts = await run_db(_get_all_country_counts)
        keyboard = []
        for row in countries:
            cid, cname = row[0], row[1]
            total, _   = counts.get(cid, (0, 0))
            keyboard.append([InlineKeyboardButton(
                f"✏️ {cname} ({total})", callback_data=f"edit_country_{cid}")])
        keyboard.append([InlineKeyboardButton(
            "🔙 Back to Admin Panel", callback_data="back_to_admin")])
        await query.edit_message_text(
            "*Edit Numbers*\n\nSelect a country to add more numbers:",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return

    # edit_country_<id>
    if data.startswith("edit_country_"):
        cid      = int(data.split("_")[-1])
        countries = await run_db(_get_countries)
        cname     = next((r[1] for r in countries if r[0] == cid), "Unknown")
        context.user_data['edit_country_id']   = cid
        context.user_data['edit_country_name'] = cname
        await query.edit_message_text(
            f"*Edit Numbers for {cname}*\n\n"
            "Please send a TXT file with numbers (one number per line):",
            parse_mode='Markdown')
        return

    # delete_specific_<id>
    if data.startswith("delete_specific_"):
        cid      = int(data.split("_")[-1])
        countries = await run_db(_get_countries)
        cname     = next((r[1] for r in countries if r[0] == cid), "Unknown")
        context.user_data['awaiting_specific_number_delete'] = True
        context.user_data['delete_target_country_id']        = cid
        await query.edit_message_text(
            f"*Delete Specific Number from {cname}*\n\n"
            "Send the phone number to delete (without + symbol):",
            parse_mode='Markdown')
        return

    # ── Panel List callbacks ───────────────────────────────────────────────────

    # Helper: build ALL panels keyboard and send it
    async def _send_all_panels_keyboard(chat_id: int):
        all_panels_db = await run_db(_get_panels)
        all_names = [pname for pname, _m in ALL_PANEL_LIST]
        panels = [p for p in all_panels_db if p['name'] in all_names]
        panels.sort(key=lambda p: all_names.index(p['name']) if p['name'] in all_names else 999)
        context.user_data.pop('panel_view_active', None)
        context.user_data.pop('panel_list_multiple_active', None)
        context.user_data.pop('panel_list_source', None)
        context.user_data['panel_list_active'] = True
        panel_btns = [KeyboardButton(_panel_label(p['name'])) for p in panels]
        rows = [panel_btns[i:i+2] for i in range(0, len(panel_btns), 2)]
        rows.append([KeyboardButton("🔙 Back to Admin Panel")])
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📋 *Panel List* ({len(panels)} panels)\n\nSelect a panel:",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True))

    # panel_list — show ALL panels in mobile keyboard
    if data == "panel_list":
        await query.answer()
        await _send_all_panels_keyboard(query.message.chat_id)
        return

    # panel_list_main — show ALL panels in mobile keyboard
    if data == "panel_list_main":
        await query.answer()
        await _send_all_panels_keyboard(query.message.chat_id)
        return

    # panel_list_multiple — show ALL panels in mobile keyboard
    if data == "panel_list_multiple":
        await query.answer()
        await _send_all_panels_keyboard(query.message.chat_id)
        return

    # panel_list_kb — show ALL panels in mobile keyboard
    if data == "panel_list_kb":
        await query.answer()
        await _send_all_panels_keyboard(query.message.chat_id)
        return

    # panel_list_kb_multiple — show ALL panels in mobile keyboard
    if data == "panel_list_kb_multiple":
        await query.answer()
        await _send_all_panels_keyboard(query.message.chat_id)
        return

    # panel_view_<name> — show Latest Message button directly
    if data.startswith("panel_view_"):
        pname  = data[len("panel_view_"):]
        panel  = await run_db(_get_panel_by_name, pname)
        if not panel:
            await query.edit_message_text("❌ Panel not found.")
            return
        keyboard = [
            [InlineKeyboardButton("📨 Latest Message",
                                  callback_data=f"panel_msgs_{pname}")],
            [InlineKeyboardButton("change user/pass",
                                  callback_data=f"panel_edit_cred_{pname}")],
            [InlineKeyboardButton("🔙 Back to Panel List", callback_data="panel_list")],
        ]
        db_user = _resolve_panel_user(panel, pname)
        await query.edit_message_text(
            f"🖥️ *{_md_escape(pname)}*\n\n"
            f"👤 Username: `{db_user}`\n"
            f"🔗 URL: `{panel['base_url']}`\n\n"
            "Choose an option:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # panel_edit_cred_<name> — start credential editing flow via inline button
    if data.startswith("panel_edit_cred_"):
        pname = data[len("panel_edit_cred_"):]
        await query.answer()
        context.user_data['awaiting_cred_panel']    = pname
        context.user_data.pop('awaiting_cred_username', None)
        context.user_data['panel_list_active']      = True
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"🔑 *Change User/Pass — {_md_escape(pname)}*\n\n"
                f"Send the *new username* for this panel:\n\n"
                "_Send /cancel to cancel._"
            ),
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # panel_msgs_<name> — show SINGLE latest message + start 3-sec auto-refresh
    if data.startswith("panel_msgs_"):
        pname = data[len("panel_msgs_"):]
        chat_id = query.message.chat_id

        # Cancel any existing refresh task for this chat
        old_task = _refresh_tasks.pop(chat_id, None)
        if old_task and not old_task.done():
            old_task.cancel()

        # Fetch from monitor's in-memory record (updated every 3 s by background poller)
        rec = await asyncio.to_thread(get_panel_latest_today, pname)

        if not rec:
            await query.edit_message_text(
                f"📭 *{_md_escape(pname)} — No SMS Today*\n\n"
                "No SMS messages have been received from this panel today.\n\n"
                "Panel may not be logged in or has no SMS yet.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data=f"panel_view_{pname}")
                ]]))
            return

        text = _format_panel_latest(rec, pname=pname)
        if len(text) > 4000:
            text = text[:3990] + "\n…"

        try:
            sent_msg = await query.edit_message_text(
                text,
                parse_mode='Markdown',
                reply_markup=_STOP_MARKUP,
            )
        except Exception:
            plain = text.replace('`', "'").replace('*', '').replace('_', '').replace('[', '(')
            if len(plain) > 4000:
                plain = plain[:3990] + "\n…"
            sent_msg = await query.edit_message_text(plain, reply_markup=_STOP_MARKUP)

        # Start background auto-refresh task
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            _refresh_latest_msg_loop(
                chat_id=chat_id,
                message_id=sent_msg.message_id,
                bot=context.bot,
                last_rec_id=rec.get('id', 0),
                pname=pname,
            )
        )
        _refresh_tasks[chat_id] = task
        return

    # stop_refresh — cancel the auto-refresh background task
    if data == "stop_refresh":
        chat_id = query.message.chat_id
        task = _refresh_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
        await query.edit_message_text(
            "⏹ *Auto-Refresh stopped.*\n\nGo back to the panel to view again.",
            parse_mode='Markdown',
        )
        return

    # ── Extra Groups callbacks ─────────────────────────────────────────────────
    if data == "eg_add":
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        context.user_data['awaiting_extra_group_id'] = True
        await query.message.reply_text(
            "Enter the Group Chat ID. (Example: -1001234567890)\n\nSend /cancel to cancel."
        )
        return

    if data == "eg_remove_list":
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        groups = await run_db(_get_all_extra_groups)
        if not groups:
            await query.edit_message_text("🗑️ No groups available to remove.")
            return
        kb_rows = []
        for g in groups:
            kb_rows.append([InlineKeyboardButton(
                f"🗑️ {g['title']} ({g['chat_id']})",
                callback_data=f"eg_del_{g['chat_id']}",
            )])
        kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data="eg_back")])
        await query.edit_message_text(
            "🗑️ *Remove Group*\n\nWhich group do you want to remove?",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(kb_rows),
        )
        return

    if data == "eg_back":
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        groups = await run_db(_get_all_extra_groups)
        if not groups:
            msg = "📢 *Extra Groups*\n\nNo extra groups have been added yet."
        else:
            lines = []
            for g in groups:
                try:
                    await context.bot.get_chat(g['chat_id'])
                    icon = "🟢"
                except Exception:
                    icon = "🔴"
                lines.append(f"{icon} *{g['title']}*\n   └ `{g['chat_id']}`")
            msg = "📢 *Extra Groups*\n\n" + "\n\n".join(lines)
        eg_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Group",    callback_data="eg_add"),
             InlineKeyboardButton("🗑️ Remove Group", callback_data="eg_remove_list")],
        ])
        await query.edit_message_text(msg, parse_mode='Markdown', reply_markup=eg_kb)
        return

    if data.startswith("eg_del_"):
        if not _is_admin(username, user_id):
            await query.answer("❌ Unauthorized.", show_alert=True)
            return
        cid = data[len("eg_del_"):]
        await run_db(_remove_extra_group, cid)
        await query.edit_message_text("✅ Group removed.")
        return



# ── Admin keyboard button handler ─────────────────────────────────────────────

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    if not update.message or not update.message.text:
        return
    username = update.effective_user.username
    user_id  = update.effective_user.id
    text     = update.message.text

    if not _is_admin(username, user_id):
        await update.message.reply_text("❌ You are not authorized to use admin commands.")
        return

    if text == "🌍 𝑪𝒐𝒖𝒏𝒕𝒓𝒚 𝑴𝒂𝒏𝒂𝒈𝒆𝒓":
        await update.message.reply_text(
            "*🌍 𝑪𝒐𝒖𝒏𝒕𝒓𝒚 𝑴𝒂𝒏𝒂𝒈𝒆𝒓*\n\nChoose an option:",
            parse_mode='Markdown', reply_markup=get_manage_numbers_keyboard())

    elif text == "👤 Manage Admins":
        await update.message.reply_text(
            "*👤 Manage Admins*\n\nChoose an option:",
            parse_mode='Markdown', reply_markup=get_manage_admins_keyboard())

    elif text == "📢 Extra Groups":
        for _flag in [
            'awaiting_extra_group_id', 'awaiting_extra_group_remove_id',
        ]:
            context.user_data.pop(_flag, None)
        msg = await _build_extra_groups_overview(context)
        await update.message.reply_text(
            msg, parse_mode='Markdown',
            reply_markup=get_extra_groups_keyboard(),
        )

    elif text == "➕ Add Group":
        context.user_data.pop('awaiting_extra_group_remove_id', None)
        context.user_data['awaiting_extra_group_id'] = True
        await update.message.reply_text(
            "➕ *Add Group*\n\n"
            "Send the *Chat ID* of the group you want to add.\n"
            "_(Example: `-1001234567890`)_\n\n"
            "⚠️ Make sure the bot is already in that group (admin recommended).\n\n"
            "_Send /cancel to cancel._",
            parse_mode='Markdown',
        )

    elif text == "🗑️ Remove Group":
        context.user_data.pop('awaiting_extra_group_id', None)
        groups = await run_db(_get_all_extra_groups)
        if not groups:
            await update.message.reply_text(
                "🗑️ *Remove Group*\n\nNo extra groups have been added yet.",
                parse_mode='Markdown',
            )
            return
        lines = [f"• `{g['chat_id']}` — {g['title']}" for g in groups]
        context.user_data['awaiting_extra_group_remove_id'] = True
        await update.message.reply_text(
            "🗑️ *Remove Group*\n\n"
            "Send the exact *Chat ID* of the group you want to remove.\n\n"
            + "\n".join(lines)
            + "\n\n_Send /cancel to cancel._",
            parse_mode='Markdown',
        )

    elif text == "⏱ Retry Interval":
        msg, has_failed = await _build_retry_login_view()
        if has_failed:
            context.user_data['awaiting_retry_login_panel'] = True
        else:
            context.user_data.pop('awaiting_retry_login_panel', None)
        await update.message.reply_text(
            msg,
            parse_mode='Markdown',
            reply_markup=get_admin_tools_keyboard())

    elif text == "🧹 Session Cleanup":
        context.user_data['awaiting_session_cleanup_panel'] = True
        msg = await _build_session_cleanup_view()
        await update.message.reply_text(msg, parse_mode='Markdown')

    elif text == "🔌 Panel Toggle":
        msg = await _build_panel_toggle_view()
        context.user_data['panel_toggle_active'] = True
        await update.message.reply_text(
            msg,
            parse_mode='Markdown',
            reply_markup=get_admin_tools_keyboard())

    elif text == "🚀 Force Start":
        context.user_data.clear()
        await _run_force_start(update, context)

    elif text == "🔗 Edit Bot Links":
        context.user_data.clear()
        lnk_number   = await run_db(_get_setting, "otp_btn_number",   "https://t.me/UnofficialNumberBOT?start=start")
        lnk_channel  = await run_db(_get_setting, "otp_btn_channel",  "https://t.me/UnofficialNumber")
        lnk_support  = await run_db(_get_setting, "bot_link_support", SUPPORT_GROUP_LINK)
        lnk_otpgroup = await run_db(_get_setting, "otp_group_link",   "https://t.me/UnofficialNumber")
        msg = (
            "🔗 *Edit Bot Links*\n\n"
            f"📲 *NUMBER:* `{lnk_number}`\n"
            f"📢 *CHANNEL:* `{lnk_channel}`\n"
            f"👥 *Support Group:* `{lnk_support}`\n"
            f"📢 *OTP Group:* `{lnk_otpgroup}`\n\n"
            "Select the link you want to change:"
        )
        await update.message.reply_text(msg, parse_mode='Markdown',
                                        reply_markup=get_edit_bot_links_keyboard())

    elif text in ("📲 NUMBER Link", "📢 CHANNEL Link", "👥 Support Group Link", "📢 OTP Group Link"):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        which_map = {
            "📲 NUMBER Link":       "number",
            "📢 CHANNEL Link":      "channel_otp",
            "👥 Support Group Link": "support_group",
            "📢 OTP Group Link":    "otp_group",
        }
        which = which_map[text]
        context.user_data['awaiting_edit_bot_link'] = which
        await update.message.reply_text(
            f"🔗 Send the new link for *{text}*:\n\n"
            "_Send /cancel to cancel._",
            parse_mode='Markdown',
            reply_markup=get_edit_bot_links_keyboard())

    elif text == "📡 Channel Join":
        channels  = await run_db(_get_required_channels)
        interval  = await run_db(_get_channel_check_interval)
        if channels:
            lines = [f"*{i}.* `{ch['name']}` — `{ch['url']}`" for i, ch in enumerate(channels, 1)]
            ch_text = "\n".join(lines)
        else:
            ch_text = "_No channels configured yet._"
        msg = (
            "📡 *Channel Join Requirement*\n\n"
            f"*Current Channels:*\n{ch_text}\n\n"
            f"⏱ *Check Interval:* `{interval}` seconds\n\n"
            "Use the buttons below to manage channels."
        )
        await update.message.reply_text(
            msg, parse_mode='Markdown',
            reply_markup=get_channel_join_keyboard(),
        )

    elif text == "➕ Add Channel":
        context.user_data['awaiting_ch_add_username'] = True
        await update.message.reply_text(
            "➕ *Add New Channel / Group*\n\n"
            "Send the channel or group *@username*.\n"
            "_Example:_ `@mychannel` or `mychannel`\n\n"
            "The bot will automatically fetch the name and link.\n\n"
            "_Send /cancel to cancel._",
            parse_mode='Markdown',
            reply_markup=get_channel_join_keyboard(),
        )

    elif text == "✏️ Edit Channel":
        channels = await run_db(_get_required_channels)
        if not channels:
            await update.message.reply_text(
                "❌ No channels to edit.",
                reply_markup=get_channel_join_keyboard(),
            )
        else:
            lines = [f"*{i}.* {ch['name']}" for i, ch in enumerate(channels, 1)]
            context.user_data['awaiting_ch_edit_index'] = True
            await update.message.reply_text(
                "✏️ *Edit Channel*\n\n"
                "Current channels:\n" + "\n".join(lines) + "\n\n"
                "Send the *number* of the channel to edit.",
                parse_mode='Markdown',
                reply_markup=get_channel_join_keyboard(),
            )

    elif text == "🗑️ Delete Channel":
        channels = await run_db(_get_required_channels)
        if not channels:
            await update.message.reply_text(
                "❌ No channels to delete.",
                reply_markup=get_channel_join_keyboard(),
            )
        else:
            lines = [f"`{ch['name']}`" for ch in channels]
            context.user_data['awaiting_ch_delete_name'] = True
            await update.message.reply_text(
                "🗑️ *Delete Channel*\n\n"
                "Current channels:\n" + "\n".join(lines) + "\n\n"
                "Type the exact *channel name* to delete it.\n"
                "_Send /cancel to cancel._",
                parse_mode='Markdown',
                reply_markup=get_channel_join_keyboard(),
            )

    elif text == "🕑 Check Interval":
        interval = await run_db(_get_channel_check_interval)
        context.user_data['awaiting_ch_interval'] = True
        await update.message.reply_text(
            f"🕑 *Member Check Interval*\n\n"
            f"Current interval: *{interval} seconds*\n\n"
            "Send the new interval in seconds (minimum 10).\n"
            "_Send /cancel to cancel._",
            parse_mode='Markdown',
            reply_markup=get_channel_join_keyboard(),
        )

    elif text == "🔙 Back to Admin Tools":
        context.user_data.pop('panel_toggle_active', None)
        await update.message.reply_text(
            "*🛠 Admin Tools*\n\nSelect an option below:",
            parse_mode='Markdown',
            reply_markup=get_admin_tools_keyboard())

    elif text == "📢 Broadcast":
        context.user_data.clear()
        context.user_data['awaiting_broadcast_message'] = True
        user_count = await run_db(_get_user_count)
        await update.message.reply_text(
            f"📢 *Broadcast Message — Powerful Mode*\n\n"
            f"👥 Total users (including auto-tracked): *{user_count}*\n"
            f"⚡ Concurrent send + auto-retry + categorised report\n\n"
            "✏️ *Mode 1 — Normal Broadcast:*\n"
            "Write / send any message (text, photo, video, voice…).\n"
            "Users will receive it *without* a forward header.\n\n"
            "📨 *Mode 2 — Forward Broadcast:*\n"
            "Forward any message from a channel or group here.\n"
            "Users will receive it *with* the original 'Forwarded from …' header.\n\n"
            "_Send /cancel to cancel._",
            parse_mode='Markdown',
            reply_markup=get_admin_keyboard())

    elif text == "⚙️ Settings":
        await update.message.reply_text(
            "*⚙️ Settings*\n\nSelect a setting below:",
            parse_mode='Markdown',
            reply_markup=get_settings_keyboard())

    elif text == "🛠 Admin Tools":
        await update.message.reply_text(
            "*🛠 Admin Tools*\n\nSelect an option below:",
            parse_mode='Markdown',
            reply_markup=get_admin_tools_keyboard())

    elif text == "🔙 Back to Admin Panel":
        await update.message.reply_text(
            "*🛠️ Admin Panel*", parse_mode='Markdown',
            reply_markup=get_admin_keyboard())

    elif text == "📲 𝑨𝒅𝒅 𝑵𝒖𝒎𝒃𝒆𝒓":
        countries = await run_db(_get_countries)
        if not countries:
            await update.message.reply_text(
                "❌ No countries available. Please use *🌐Add 𝑪𝒐𝒖𝒏𝒕𝒓𝒚* first.",
                parse_mode='Markdown',
                reply_markup=get_manage_numbers_keyboard())
            return
        counts = await run_db(_get_all_country_counts)
        lines = ["*📲 Add Number*", "", "*Available countries:*"]
        for cid, cname in countries:
            total, avail = counts.get(cid, (0, 0))
            used = total - avail
            lines.append(f"• `{cname}` — Total: {total} | Used: {used} | Available: {avail}")
        lines.append("")
        lines.append("Send the *country name* to add numbers to it:")
        context.user_data.clear()
        context.user_data['awaiting_add_numbers_country'] = True
        await update.message.reply_text(
            "\n".join(lines), parse_mode='Markdown')

    elif text == "🛠️ 𝑺𝒆𝒓𝒗𝒊𝒄𝒆𝒔":
        context.user_data.clear()
        context.user_data['service_manager_active'] = True
        msg = await _build_service_manager_text()
        await update.message.reply_text(
            msg,
            parse_mode='Markdown',
            reply_markup=get_manage_numbers_keyboard(),
        )

    elif text == "🌐Add 𝑪𝒐𝒖𝒏𝒕𝒓𝒚":
        countries = await run_db(_get_countries)
        counts    = await run_db(_get_all_country_counts)
        context.user_data.clear()
        context.user_data['awaiting_new_country_name'] = True

        lines = ["*🌐 Country Manager*", ""]
        if not countries:
            lines.append("_No countries added yet._")
            lines.append("")
        else:
            grand_total = grand_avail = 0
            for cid, cname in countries:
                total, avail = counts.get(cid, (0, 0))
                used = total - avail
                grand_total += total
                grand_avail += avail
                lines.append(
                    f"🌍 `{cname}`\n"
                    f"  ➕ Added: `{total}`  ✅ Available: `{avail}`  🔴 Used: `{used}`"
                )
                lines.append("`" + "─" * 30 + "`")
            grand_used = grand_total - grand_avail
            lines.append(
                f"\n📌 *Total Countries:* `{len(countries)}`\n"
                f"🔢 *Total Numbers:* `{grand_total}`\n"
                f"✅ *Available:* `{grand_avail}`  🔴 *Used:* `{grand_used}`"
            )
            lines.append("")

        lines.append("📝 Type a country name to add it.")
        lines.append("🗑️ To delete: type `delete` <country name>")
        lines.append("Send /cancel to cancel.")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3990] + "\n`…`"
        await update.message.reply_text(msg, parse_mode='Markdown')

    elif text == "__DISABLED_NUMBER_STATS__":
        stats = await run_db(_get_country_stats)
        if not stats:
            await update.message.reply_text(
                "📊 *Number Stats*\n\n❌ No countries or numbers found.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]]
                )
            )
            return

        grand_total = grand_avail = 0
        lines = []
        for country_name, total, avail in stats:
            grand_total += total
            grand_avail += avail
            used = total - avail
            lines.append(
                f"🌍 *{country_name}*\n"
                f"   ➕ Total: `{total}`  |  ✅ Available: `{avail}`  |  🔴 Used: `{used}`"
            )

        summary = (
            f"\n\n━━━━━━━━━━━━━━━━━━\n"
            f"📌 *Total Countries:* `{len(stats)}`\n"
            f"🔢 *Grand Total Numbers:* `{grand_total}`\n"
            f"✅ *Grand Total Available:* `{grand_avail}`\n"
            f"🔴 *Grand Total Used:* `{grand_total - grand_avail}`"
        )
        body = "\n\n".join(lines)
        full_text = f"📊 *Number Stats*\n\n{body}{summary}"

        back_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]]
        )

        if len(full_text) <= 4096:
            await update.message.reply_text(full_text, parse_mode='Markdown', reply_markup=back_kb)
        else:
            # Split into safe chunks (≤ 4096 chars each), restore header on each chunk
            header = "📊 *Number Stats*\n\n"
            chunks: list[str] = []
            current = header
            for line in lines:
                addition = line + "\n\n"
                if len(current) + len(addition) > 4096:
                    chunks.append(current.strip())
                    current = header + addition
                else:
                    current += addition
            # Attach summary to last chunk (or as its own chunk if too long)
            if len(current) + len(summary) <= 4096:
                current += summary
                chunks.append(current.strip())
            else:
                chunks.append(current.strip())
                chunks.append(summary.strip())

            for i, chunk in enumerate(chunks):
                kb = back_kb if i == len(chunks) - 1 else None
                await update.message.reply_text(chunk, parse_mode='Markdown', reply_markup=kb)

    elif text == "👥 Add Admin":
        context.user_data['awaiting_new_admin'] = True
        await update.message.reply_text(
            "👤 *Add New Admin*\n\n"
            "Enter the *User ID (UID)* of the new admin:\n\n"
            "Type /cancel to cancel.",
            parse_mode='Markdown'
        )

    elif text == "🔧 Remove Admin":
        admins = await run_db(_get_all_admins_with_details)
        if not admins:
            await update.message.reply_text("❌ No admins found.")
            return
        from config import PROTECTED_ADMINS, PROTECTED_ADMIN_IDS
        items = []
        for adm in admins:
            db_uname = adm['username'] or ''
            uid      = adm.get('user_id')
            fname    = adm.get('first_name') or 'Unknown'
            is_prot  = db_uname in PROTECTED_ADMINS or (uid and uid in PROTECTED_ADMIN_IDS)
            display  = f"{fname} (UID: {uid})" if uid else fname
            if is_prot:
                items.append(InlineKeyboardButton(f"🛡️ {display} — Protected", callback_data=f"protected_admin_{db_uname}"))
            else:
                items.append(InlineKeyboardButton(f"👤 {display}", callback_data=f"admin_info_{db_uname}"))
        keyboard = [items[i:i+2] for i in range(0, len(items), 2)]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")])
        await update.message.reply_text(
            "🔧 *Remove Admin*\n\n"
            "Select an admin to see their details, then you can remove them.\n\n"
            "🛡️ Protected admins cannot be removed.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    elif text == "👥 Users":
        await update.message.reply_text(
            "*👥 Users*\n\nSelect an option below:",
            parse_mode='Markdown',
            reply_markup=get_users_keyboard())

    elif text == "🔍 User Info":
        if not _is_admin(username, user_id):
            return
        context.user_data['awaiting_user_info_id'] = True
        await update.message.reply_text(
            "🔍 *User Info Lookup*\n\n"
            "Enter the Telegram *User ID* (numeric) of the user you want to view:",
            parse_mode='Markdown',
            reply_markup=get_users_keyboard())

    elif text == "📈 User Stats":
        from datetime import timezone, timedelta as _td
        _tz_bd = timezone(_td(hours=6))
        now_bd = datetime.now(_tz_bd)
        now_str = now_bd.strftime("%d %b %Y, %I:%M %p")

        top5 = await run_db(_get_top_users_detailed, 5)

        lines = [
            "`📈 Top 5 User Stats`",
            "",
        ]
        for i, u in enumerate(top5, 1):
            name = u['display_name'] or f"ID:{u['user_id']}"
            bal  = u.get('balance', 0.0)
            lines.append(f"`{'─'*28}`")
            lines.append(f"`🏅 #{i}  {name}`")
            lines.append(f"`📞 Numbers Used   : {u['numbers_used']}`")
            lines.append(f"`📨 Msgs Received  : {u['msgs_received']}`")
            lines.append(f"`👥 Referrals      : {u['referral_count']}`")
            lines.append(f"`💰 Balance        : {bal:.2f} ৳`")
            lines.append("")

        lines.append(f"`{'─'*28}`")
        lines.append(f"`🕐 {now_str} (UTC+6)`")

        msg = "\n".join(lines)
        await update.message.reply_text(msg, parse_mode='Markdown',
                                        reply_markup=get_users_keyboard())

    elif text == "👤 User Count":
        user_count = await run_db(_get_user_count)
        now = datetime.now().strftime("%d %B %Y, %I:%M %p")
        msg = (
            f"╔══════════════════════╗\n"
            f"║   👥 USER STATISTICS   ║\n"
            f"╚══════════════════════╝\n\n"
            f"📊 *Total Registered Users*\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃  👤 Total Users: *{user_count}*\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🕐 *Report Time:* {now}\n\n"
            f"📁 Excel file with all user data is attached below."
        )
        await update.message.reply_text(msg, parse_mode='Markdown',
                                        reply_markup=get_users_keyboard())
        if user_count > 0:
            excel_buffer = await run_db(generate_users_excel)
            await update.message.reply_document(
                document=excel_buffer,
                filename=f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                caption=f"📋 Full user list — {user_count} users total")
        else:
            await update.message.reply_text("⚠️ No users registered yet.",
                                            reply_markup=get_users_keyboard())

    elif text == "🔄 𝑹𝒆𝒔𝒆𝒕 𝑵𝒖𝒎𝒃𝒆𝒓":
        countries = await run_db(_get_countries)
        counts    = await run_db(_get_all_country_counts)
        # Only countries that actually have numbers
        active = [(cid, cname) for cid, cname in countries
                  if counts.get(cid, (0, 0))[0] > 0]
        if not active:
            await update.message.reply_text(
                "❌ No countries with numbers available.",
                reply_markup=get_manage_numbers_keyboard())
            return
        lines = ["*🔄 𝑹𝒆𝒔𝒆𝒕 𝑵𝒖𝒎𝒃𝒆𝒓*", "", "*Available countries:*"]
        for cid, cname in active:
            total, avail = counts.get(cid, (0, 0))
            used = total - avail
            lines.append(f"• `{cname}` — Used: {used}/{total}")
        lines.append("")
        lines.append("To reset used numbers of a country, send:")
        lines.append("`reset` <country name>")
        lines.append("")
        lines.append("⚠️ This will make all used numbers available again.")
        context.user_data.clear()
        context.user_data['awaiting_reset_country_name'] = True
        await update.message.reply_text(
            "\n".join(lines), parse_mode='Markdown')

    elif text == "☎️ Get Number":
        await show_countries(update, context)

    elif text == "📋 Panel List":
        # Show ALL panels directly in mobile keyboard
        context.user_data.pop('panel_view_active', None)
        context.user_data.pop('panel_category_active', None)
        context.user_data.pop('panel_list_category', None)
        context.user_data.pop('panel_list_source', None)
        context.user_data.pop('panel_list_multiple_active', None)
        context.user_data['panel_list_active'] = True

        all_panels_db = await run_db(_get_panels)
        all_names     = [pname for pname, _m in ALL_PANEL_LIST]
        panels        = [p for p in all_panels_db if p['name'] in all_names]
        panels.sort(key=lambda p: all_names.index(p['name']) if p['name'] in all_names else 999)
        panel_btns = [KeyboardButton(_panel_label(p['name'])) for p in panels]
        rows = [panel_btns[i:i+2] for i in range(0, len(panel_btns), 2)]
        rows.append([KeyboardButton("🔙 Back to Admin Panel")])
        await update.message.reply_text(
            f"📋 *Panel List* ({len(panels)} panels)\n\nSelect a panel:",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True))

    elif text == "🔄 Reload Interval":
        context.user_data['awaiting_reload_interval_panel'] = True
        # List all panel names in mono format
        lines = [f"• `{pname}`" for pname, _m in ALL_PANEL_LIST]
        await update.message.reply_text(
            "🔄 *Reload Interval*\n\n"
            "Below are all configured panels. Each panel polls its source "
            "for new SMS messages every N seconds.\n\n"
            "Send the *exact name* of the panel whose reload interval you "
            "want to change:\n\n"
            + "\n".join(lines)
            + "\n\n_Send /cancel to abort._",
            parse_mode='Markdown')

    elif text in ("🎯 OTP Bonus Settings", "🎯 OTP Bonus"):
        settings = await run_db(_get_otp_bonus_settings)
        status   = "✅ Active" if settings['enabled'] else "❌ Inactive"
        await update.message.reply_text(
            f"🎯 *OTP Bonus Settings*\n\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 🔘 Status: *{status}*\n"
            f"┃ 💰 Bonus per OTP: *৳ {settings['amount']:.2f}*\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"ℹ️ Select an option from the keyboard below.",
            parse_mode='Markdown',
            reply_markup=get_otp_bonus_keyboard()
        )

    elif text == "🔛 OTP Bonus Toggle":
        settings  = await run_db(_get_otp_bonus_settings)
        new_state = not settings['enabled']
        await run_db(_toggle_otp_bonus, new_state)
        settings2 = await run_db(_get_otp_bonus_settings)
        status    = "✅ Active" if new_state else "❌ Inactive"
        await update.message.reply_text(
            f"🎯 *OTP Bonus Updated!*\n\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 🔘 Status: *{status}*\n"
            f"┃ 💰 Bonus per OTP: *৳ {settings2['amount']:.2f}*\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━",
            parse_mode='Markdown',
            reply_markup=get_otp_bonus_keyboard()
        )

    elif text == "💰 Set Bonus Amount":
        context.user_data['awaiting_otp_bonus_amount'] = True
        await update.message.reply_text(
            "💰 *Set OTP Bonus Amount*\n\n"
            "Enter how much bonus a user receives per OTP notification:\n"
            "_(Example: 2 or 5.50)_\n\n"
            "Send /cancel to cancel.",
            parse_mode='Markdown',
            reply_markup=get_otp_bonus_keyboard()
        )

    elif text == "👤 Edit Balance":
        context.user_data['awaiting_balance_user_id'] = True
        await update.message.reply_text(
            "👤 *Edit User Balance*\n\n"
            "Enter the *Telegram User ID* of the user whose balance you want to edit:\n\n"
            "Send /cancel to cancel.",
            parse_mode='Markdown'
        )

    elif text == "🔙 Back to Settings":
        await update.message.reply_text(
            "*⚙️ Settings*\n\nSelect a setting below:",
            parse_mode='Markdown',
            reply_markup=get_settings_keyboard()
        )

    elif text in ("🎁 Referral Settings", "🎁 Referral"):
        settings = await run_db(_get_referral_settings)
        min_wd   = await run_db(_get_min_withdraw)
        status   = "✅ Active" if settings['enabled'] else "❌ Inactive"
        top      = await run_db(_get_top_referrers, 5)
        pending  = await run_db(_get_pending_withdraws)
        top_text = ""
        for i, r in enumerate(top, 1):
            name = f"@{r['username']}" if r['username'] else r['first_name'] or str(r['user_id'])
            top_text += f"  {i}. {name} — {r['count']} referral(s) | ৳ {r['earned']:.2f}\n"
        await update.message.reply_text(
            f"🎁 *Referral Settings*\n\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 🔘 Status: *{status}*\n"
            f"┃ 💰 Bonus per referral: *৳ {settings['bonus']:.2f}*\n"
            f"┃ 📤 Min Withdraw: *৳ {min_wd:.2f}*\n"
            f"┃ 💸 Pending Withdraws: *{len(pending)}*\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏆 *Top Referrers:*\n{top_text if top_text else '  No one has referred yet.'}\n"
            f"Select an option from the keyboard below.",
            parse_mode='Markdown',
            reply_markup=get_referral_keyboard()
        )

    elif text == "🔛 Referral Toggle":
        settings  = await run_db(_get_referral_settings)
        new_state = not settings['enabled']
        await run_db(_toggle_referral, new_state)
        settings2 = await run_db(_get_referral_settings)
        min_wd2   = await run_db(_get_min_withdraw)
        status    = "✅ Active" if new_state else "❌ Inactive"
        await update.message.reply_text(
            f"🎁 *Referral Updated!*\n\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 🔘 Status: *{status}*\n"
            f"┃ 💰 Bonus per referral: *৳ {settings2['bonus']:.2f}*\n"
            f"┃ 📤 Min Withdraw: *৳ {min_wd2:.2f}*\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━",
            parse_mode='Markdown',
            reply_markup=get_referral_keyboard()
        )

    elif text == "💰 Set Referral Bonus":
        context.user_data['awaiting_ref_bonus'] = True
        await update.message.reply_text(
            "💰 *Change Bonus Amount*\n\n"
            "Enter how much bonus to give per referral:\n"
            "_(Example: 10 or 25.50)_\n\n"
            "Send /cancel to cancel.",
            parse_mode='Markdown',
            reply_markup=get_referral_keyboard()
        )

    elif text == "📤 Set Min Withdraw":
        context.user_data['awaiting_min_withdraw'] = True
        await update.message.reply_text(
            "📤 *Set Minimum Withdraw Amount*\n\n"
            "Enter the minimum amount a user can withdraw:\n"
            "_(Example: 50 or 100)_\n\n"
            "Send /cancel to cancel.",
            parse_mode='Markdown',
            reply_markup=get_referral_keyboard()
        )

    elif text == "💸 Pending Withdraws":
        pending = await run_db(_get_pending_withdraws)
        if not pending:
            await update.message.reply_text(
                "✅ No pending withdrawals.",
                reply_markup=get_referral_keyboard()
            )
        else:
            for req in pending[:5]:
                name   = f"@{req['username']}" if req['username'] else req['first_name'] or str(req['user_id'])
                markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve_{req['id']}"),
                     InlineKeyboardButton("❌ Reject",  callback_data=f"wd_reject_{req['id']}")],
                ])
                await update.message.reply_text(
                    f"💸 *Withdraw Request #{req['id']}*\n\n"
                    f"👤 User: *{name}* (`{req['user_id']}`)\n"
                    f"💰 Amount: *৳ {req['amount']:.2f}*\n"
                    f"📱 Method: *{req['method']}*\n"
                    f"📞 Account: `{req['account']}`\n"
                    f"🕐 Time: {req['created_at']}",
                    parse_mode='Markdown',
                    reply_markup=markup
                )
            if len(pending) > 5:
                await update.message.reply_text(
                    f"ℹ️ Total *{len(pending)}* pending, showing first 5.",
                    parse_mode='Markdown',
                    reply_markup=get_referral_keyboard()
                )

    elif text == "📊 𝑩𝒐𝒕 𝑺𝒕𝒂𝒕𝒊𝒔𝒕𝒊𝒄𝒔":
        stats = await run_db(_get_bot_overview_stats)
        now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")

        # ── Country breakdown ──────────────────────────────────────────────────
        country_lines = []
        for i, cr in enumerate(stats["country_rows"], 1):
            country_lines.append(
                f"*{i}.* *{cr['name']}*\n"
                f"    ▸ *Total Added:* *{cr['total']}*\n"
                f"    ▸ *Available:* *{cr['available']}*"
            )
        countries_block = (
            "\n\n".join(country_lines) if country_lines else "*No countries added yet.*"
        )

        # ── Panel login status ─────────────────────────────────────────────────
        panel_statuses = await run_db(_get_all_panel_statuses)
        statuses_by_name = {s['panel_name']: s for s in (panel_statuses or [])}
        total_panels = len(ALL_PANEL_LIST)
        success_count = 0
        failed_count = 0
        disabled_count = 0
        for pname, _m in ALL_PANEL_LIST:
            is_en = await run_db(_is_panel_enabled, pname)
            if not is_en:
                disabled_count += 1
                continue
            s = statuses_by_name.get(pname)
            if s and s.get('logged_in'):
                success_count += 1
            else:
                failed_count += 1

        msg = (
            f"📊 *Bot Statistics*\n"
            f"🕐 *Updated: {now_str}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"

            f"👥 *Total Users: {stats['total_users']}*\n\n"

            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥️ *Total Panels: {total_panels}*\n"
            f"✅ *Login Success: {success_count}*\n"
            f"❌ *Login Failed: {failed_count}*\n"
            + (f"🚫 *Disabled: {disabled_count}*\n" if disabled_count > 0 else "")
            + f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 *Total Countries: {stats['total_countries']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{countries_block}\n\n"

            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎁 *Total Referrals: {stats['total_referrals']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━"
        )

        if len(msg) > 4096:
            msg = msg[:4090] + "\n…"

        await update.message.reply_text(
            msg, parse_mode='Markdown',
            reply_markup=get_admin_keyboard()
        )

    elif text == "🔢 Number Limit":
        current = await run_db(_get_number_limit)
        await update.message.reply_text(
            f"🔢 *Number Limit Settings*\n\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 📊 Current Limit: *{current}* number(s)\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"ℹ️ Enter how many numbers each user gets at a time.\n"
            f"Example: *1* gives 1 number, *3* gives 3 numbers.\n\n"
            f"Send /cancel to cancel.",
            parse_mode='Markdown',
            reply_markup=get_settings_keyboard()
        )
        context.user_data['awaiting_number_limit'] = True

    elif text == "🌍 Country OTP Bonus":
        try:
            countries  = await run_db(_get_countries)
            counts     = await run_db(_get_all_country_counts)
            bonuses    = await run_db(_get_all_country_otp_bonuses)
            global_cfg = await run_db(_get_otp_bonus_settings)
            keyboard   = []
            lines      = []
            for row in countries:
                cid, cname = row[0], row[1]
                total, _   = counts.get(cid, (0, 0))
                if total == 0:
                    continue
                custom    = bonuses.get(cid)
                bonus_str = f"৳{custom:.2f}" if custom is not None else "default"
                keyboard.append([InlineKeyboardButton(
                    f"🌍 {cname} ({total} numbers) — {bonus_str}",
                    callback_data=f"cob_sel_{cid}"
                )])
                if custom is not None:
                    lines.append(f"  `{cname}` ({total}): ৳ {custom:.2f} (custom)")
                else:
                    lines.append(f"  `{cname}` ({total}): ৳ {global_cfg['amount']:.2f} (default)")
            if not keyboard:
                await update.message.reply_text(
                    "❌ No numbers have been added to any country yet.",
                    reply_markup=get_settings_keyboard()
                )
                return
            keyboard.append([InlineKeyboardButton("🔙 Close", callback_data="cob_close")])
            summary = "\n".join(lines) if lines else "(no settings)"
            msg = (
                f"🌍 *Country OTP Bonus Settings*\n\n"
                f"┣━━━━━━━━━━━━━━━━━━━━━\n"
                f"┃ 🌐 Global Default: *৳ {global_cfg['amount']:.2f}*\n"
                f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{summary}\n\n"
                f"Select a country:"
            )
            await update.message.reply_text(
                msg,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Country OTP Bonus handler error: {e}", exc_info=True)
            await update.message.reply_text(
                f"❌ Failed to load Country OTP Bonus: `{e}`",
                parse_mode='Markdown',
                reply_markup=get_settings_keyboard()
            )

    elif text == "🗑️ Reset All Users":
        context.user_data['awaiting_reset_users_confirm'] = True
        await update.message.reply_text(
            "⚠️ *Warning — Data Reset!*\n\n"
            "When this operation runs:\n\n"
            "📦 *First, a ZIP backup file will be sent* containing:\n"
            "  • All user info and balances\n"
            "  • Referral logs\n"
            "  • Withdraw requests\n"
            "  • OTP bonus logs\n"
            "  • Number assignments & OTP deliveries\n"
            "  • SMS logs (last 5000)\n\n"
            "🗑️ *Then the following data will be deleted:*\n"
            "  • All user balances → reset to 0\n"
            "  • Referral logs\n"
            "  • Withdraw requests\n"
            "  • OTP bonus logs\n"
            "  • Number assignments & OTP deliveries\n"
            "  • SMS message history\n\n"
            "✅ *What will NOT be deleted (unchanged):*\n"
            "  • All user accounts and their info\n"
            "  • Countries, numbers, admins, panels, settings\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "To confirm, type exactly:\n"
            "`YES DELETE`\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Type anything else to cancel.",
            parse_mode='Markdown'
        )


# ── User keyboard button handler ──────────────────────────────────────────────

async def handle_user_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    if not update.message or not update.message.text:
        return
    # Auto-track every user-panel button click — even if user never /start'd
    await _ensure_user_tracked(update)
    text    = update.message.text
    user_id = update.effective_user.id

    if not await _require_membership(update, context):
        return

    if text in ("☎️ Get Number", "Get Numbers"):
        await show_countries(update, context)
    elif text == "🌍 Available Country":
        await show_stats(update, context)
    elif text == "👥 Support Group":
        await handle_support_platform(update, context)
    elif text == "💰 My Balance":
        balance      = await run_db(_get_user_balance, user_id)
        ref_count    = await run_db(_get_referral_count, user_id)
        total_earned = await run_db(_get_referral_total_earned, user_id)
        settings     = await run_db(_get_referral_settings)
        min_wd       = await run_db(_get_min_withdraw)
        otp_stats    = await run_db(_get_user_otp_bonus_stats, user_id)
        bot_info     = await context.bot.get_me()
        ref_link     = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
        from telegram import CopyTextButton as _CopyBtn
        ref_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Copy Referral Link", copy_text=_CopyBtn(text=ref_link))]
        ])
        await update.message.reply_text(
            f"💰 *Your Balance*\n\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 💵 Current Balance: *৳ {balance:.2f}*\n"
            f"┃ 📤 Min Withdraw: *৳ {min_wd:.2f}*\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎁 *Referral Bonus*\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 👥 Total Referrals: *{ref_count}*\n"
            f"┃ 💸 Total Referral Earnings: *৳ {total_earned:.2f}*\n"
            f"┃ 💰 Bonus per Referral: *৳ {settings['bonus']:.2f}*\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎯 *OTP Bonus*\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃ 📩 OTP Bonuses Today: *{otp_stats['today_count']}* time(s)\n"
            f"┃ 💵 Today's OTP Earnings: *৳ {otp_stats['today_earned']:.2f}*\n"
            f"┃ 📊 Total OTP Bonuses: *{otp_stats['total_count']}* time(s)\n"
            f"┃ 💰 Total OTP Earnings: *৳ {otp_stats['total_earned']:.2f}*\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔗 *Your Referral Link:*\n`{ref_link}`\n\n"
            f"Share the link with friends and earn bonuses! 🎉",
            parse_mode='Markdown',
            reply_markup=ref_markup,
        )
    elif text == "💸 Withdraw":
        balance  = await run_db(_get_user_balance, user_id)
        min_wd   = await run_db(_get_min_withdraw)
        if balance < min_wd:
            await update.message.reply_text(
                f"❌ *Withdrawal Not Available*\n\n"
                f"💰 Your current balance: *৳ {balance:.2f}*\n"
                f"📤 Minimum withdraw amount: *৳ {min_wd:.2f}*\n\n"
                f"Refer more friends to increase your balance!",
                parse_mode='Markdown'
            )
            return
        context.user_data['awaiting_withdraw_method'] = True
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Binance", callback_data="wd_method_binance")],
            [InlineKeyboardButton("📱 bKash",   callback_data="wd_method_bkash")],
            [InlineKeyboardButton("📱 Nagad",   callback_data="wd_method_nagad")],
            [InlineKeyboardButton("❌ Cancel",  callback_data="wd_cancel")],
        ])
        await update.message.reply_text(
            f"💸 *Withdraw Request*\n\n"
            f"💰 Your balance: *৳ {balance:.2f}*\n"
            f"📤 Minimum: *৳ {min_wd:.2f}*\n\n"
            f"Select your preferred payment method:",
            parse_mode='Markdown',
            reply_markup=markup
        )

    elif text == "🏆 Top Users":
        from datetime import timezone, timedelta as _td
        _tz_bd = timezone(_td(hours=6))
        now_str = datetime.now(_tz_bd).strftime("%d %b %Y, %I:%M %p")

        my_info   = await run_db(_get_user_info_by_id, user_id)
        my_otp    = await run_db(_get_user_otp_bonus_stats, user_id)
        my_bal    = my_info['balance'] if my_info else 0.0
        my_name   = (f"@{my_info['username']}" if my_info and my_info.get('username')
                     else (my_info.get('first_name') if my_info else str(user_id)))
        my_msgs   = my_otp.get('total_count', 0) if my_otp else 0

        top5 = await run_db(_get_top_users_detailed, 5)

        lines = [
            f"`{'─'*28}`",
            f"`👤 My Stats`",
            f"`📛 Name       : {my_name}`",
            f"`💎 UID        : {user_id}`",
            f"`📨 OTP Msgs   : {my_msgs}`",
            f"`💰 Balance    : {my_bal:.2f} ৳`",
            "",
            "*🏆 Top 5 Users*",
            "",
        ]
        for i, u in enumerate(top5, 1):
            uname = u['display_name'] or f"ID:{u['user_id']}"
            bal   = u.get('balance', 0.0)
            lines.append(f"`{'─'*28}`")
            lines.append(f"`🏅 #{i}  {uname}`")
            lines.append(f"`📨 OTP Msgs   : {u['msgs_received']}`")
            lines.append(f"`💰 Balance    : {bal:.2f} ৳`")
            lines.append("")

        lines.append(f"`{'─'*28}`")
        lines.append(f"`🕐 {now_str} (UTC+6)`")

        refresh_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="top_users_refresh")]
        ])
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode='Markdown',
            reply_markup=refresh_markup,
        )


async def handle_support_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support_link = await run_db(_get_setting, "bot_link_support", SUPPORT_GROUP_LINK)
    msg = (
        "🌟 *Welcome to Our Support Platform!*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💬 Need help? Have questions? We're here for you!\n\n"
        "Our dedicated support team is always ready to assist you "
        "with any issues or inquiries you may have.\n\n"
        "🔹 *Fast response time*\n"
        "🔹 *Friendly & professional support*\n"
        "🔹 *Available around the clock*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👇 Click the button below to join our support group!"
    )
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🤝 Bot Join Support Plus", url=support_link)
    ]])
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=markup)


# ── Document handler (add numbers / edit numbers) ─────────────────────────────

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    await _ensure_user_tracked(update)
    username = update.effective_user.username
    user_id  = update.effective_user.id
    if not _is_admin(username, user_id):
        await update.message.reply_text("❌ Unauthorized access.")
        return

    edit_mode = (context.user_data.get('edit_country_id') is not None
                 and context.user_data.get('edit_country_name'))
    add_mode  = (context.user_data.get('awaiting_numbers_file')
                 and context.user_data.get('current_country_name'))

    if edit_mode:
        try:
            cid   = context.user_data['edit_country_id']
            cname = context.user_data['edit_country_name']
            doc   = await update.message.document.get_file()
            raw   = await doc.download_as_bytearray()
            nums  = [n.strip() for n in raw.decode('utf-8').replace('\r', '').split('\n') if n.strip()]
            if not nums:
                await update.message.reply_text("❌ No valid numbers found in the file.")
                return
            added        = await run_db(_add_numbers_to_country, cid, nums)
            total, avail = await run_db(_get_numbers_count_by_country, cid)
            context.user_data.pop('edit_country_id', None)
            context.user_data.pop('edit_country_name', None)
            await update.message.reply_text(
                f"✅ *{added}* number(s) added to *{cname}*!\n\n"
                f"📊 Total: {total} | Available: {avail}",
                parse_mode='Markdown',
                reply_markup=get_manage_numbers_keyboard())
        except Exception as e:
            logger.error(f"handle_document edit_mode: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    elif add_mode:
        try:
            cname = context.user_data['current_country_name']
            doc   = await update.message.document.get_file()
            raw   = await doc.download_as_bytearray()
            nums  = [n.strip() for n in raw.decode('utf-8').replace('\r', '').split('\n') if n.strip()]
            if not nums:
                await update.message.reply_text("❌ No valid numbers found in the file.")
                return
            await run_db(_add_country, cname)
            cid = await run_db(_get_country_id_by_name, cname)
            if not cid:
                await update.message.reply_text("❌ Error: Country not found after creation.")
                return
            added        = await run_db(_add_numbers_to_country, cid, nums)
            total, avail = await run_db(_get_numbers_count_by_country, cid)
            context.user_data.pop('awaiting_numbers_file', None)
            context.user_data.pop('current_country_name', None)
            await update.message.reply_text(
                f"✅ *{added}* number(s) added to *{cname}*!\n\n"
                f"📊 Total: {total} | Available: {avail}",
                parse_mode='Markdown',
                reply_markup=get_manage_numbers_keyboard())
        except Exception as e:
            logger.error(f"handle_document add_mode: {e}")
            await update.message.reply_text(f"❌ Error: {e}")
    else:
        await update.message.reply_text(
            "❌ Please press *📲 Add Number* first, enter the country name, then send the file.",
            parse_mode='Markdown')


# ── General text handler ──────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    # Auto-track every text interaction so the user appears in admin User Count
    # and in the Broadcast/Force Start audience even without /start.
    await _ensure_user_tracked(update)
    username = update.effective_user.username
    user_id  = update.effective_user.id
    text     = (update.message.text or "").strip()
    if not text:
        return

    # ── Membership gate (non-admins only) ─────────────────────────────────────
    if not _is_admin(username, user_id):
        if not await _require_membership(update, context):
            return

    # ── Cancel any pending "awaiting input" state when a known menu button
    #    is pressed (so navigation never gets stuck in a stale prompt). ────────
    _MENU_BUTTONS = {
        "🔙 Back to Admin Panel", "🔙 Back to Panel List",
        "🔙 Back", "🔙 Back to Settings",
        "🛠 Admin Tools", "🌍 𝑪𝒐𝒖𝒏𝒕𝒓𝒚 𝑴𝒂𝒏𝒂𝒈𝒆𝒓", "👤 Manage Admins",
        "👥 Users", "👤 User Count", "📈 User Stats", "🔍 User Info", "☎️ Get Number", "📋 Panel List",
        "⚙️ Settings", "⏱ Retry Interval", "🧹 Session Cleanup",
        "🔌 Panel Toggle", "🔄 Reload Interval",
        "📢 Broadcast", "🚀 Force Start", "🔗 Edit Bot Links",
        "📲 NUMBER Link", "📢 CHANNEL Link",
        "🔙 Back to Admin Tools",
        "🔢 Number Limit", "🎁 OTP Bonus", "🎯 OTP Bonus", "📢 Extra Groups",
        "➕ Add Group", "🗑️ Remove Group",
        "🌐Add 𝑪𝒐𝒖𝒏𝒕𝒓𝒚", "📲 𝑨𝒅𝒅 𝑵𝒖𝒎𝒃𝒆𝒓", "🔄 𝑹𝒆𝒔𝒆𝒕 𝑵𝒖𝒎𝒃𝒆𝒓", "🛠️ 𝑺𝒆𝒓𝒗𝒊𝒄𝒆𝒔",
        "change user/pass",
        "📊 View Stats", "📊 Login & View Stats", "/start", "/cancel",
        "🔛 OTP Bonus Toggle", "💰 Set Bonus Amount",
        "🔛 Referral Toggle", "💰 Set Referral Bonus", "📤 Set Min Withdraw",
        "💸 Pending Withdraws", "👤 Edit Balance",
        "📊 𝑩𝒐𝒕 𝑺𝒕𝒂𝒕𝒊𝒔𝒕𝒊𝒄𝒔",
        "📡 Channel Join",
        "➕ Add Channel", "✏️ Edit Channel", "🗑️ Delete Channel", "🕑 Check Interval",
    }
    if text in _MENU_BUTTONS:
        for _flag in [
            'awaiting_extra_group_id', 'awaiting_extra_group_remove_id',
            'awaiting_reset_country_name',
            'awaiting_add_numbers_country', 'awaiting_reset_users_confirm',
            'awaiting_number_limit', 'awaiting_country_name',
            'awaiting_new_country_name', 'awaiting_numbers_file',
            'awaiting_admin_username',
            'awaiting_otp_bonus_amount', 'awaiting_otp_daily_limit',
            'awaiting_country_otp_bonus_amount',
            'awaiting_broadcast_message',
            'awaiting_edit_bot_link',
            'awaiting_ch_add_username',
            'awaiting_ch_edit_index', 'awaiting_ch_edit_name', 'awaiting_ch_edit_url',
            'ch_edit_index', 'ch_edit_name',
            'awaiting_ch_delete_name', 'awaiting_ch_interval',
            'awaiting_svc_name', 'svc_add_country_id', 'svc_add_country_name',
            'awaiting_user_info_id',
            'service_manager_active',
        ]:
            context.user_data.pop(_flag, None)
        context.user_data.pop('current_country_name', None)
        context.user_data.pop('edit_country_id', None)
        context.user_data.pop('edit_country_name', None)

    # ── Extra Group add flow ───────────────────────────────────────────────────
    if context.user_data.get('awaiting_extra_group_id'):
        context.user_data.pop('awaiting_extra_group_id')
        raw = text
        try:
            chat = await context.bot.get_chat(raw)
            await run_db(_add_extra_group, str(chat.id), chat.title or raw)
            await update.message.reply_text(
                f"✅ *Group Added!*\n\n"
                f"🏷️ Name: `{chat.title}`\n🆔 ID: `{chat.id}`",
                parse_mode='Markdown',
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Group not found or bot is not in that group.\nError: {e}"
            )
        # Re-show the overview + Extra Groups keyboard
        msg = await _build_extra_groups_overview(context)
        await update.message.reply_text(
            msg, parse_mode='Markdown',
            reply_markup=get_extra_groups_keyboard(),
        )
        return

    # ── Extra Group remove flow ────────────────────────────────────────────────
    if context.user_data.get('awaiting_extra_group_remove_id'):
        context.user_data.pop('awaiting_extra_group_remove_id')
        raw = (text or "").strip()
        groups = await run_db(_get_all_extra_groups)
        match = next(
            (g for g in groups if str(g['chat_id']) == raw),
            None,
        )
        if not match:
            await update.message.reply_text(
                f"❌ Chat ID `{raw}` not found in the list. Please try again.",
                parse_mode='Markdown',
            )
        else:
            await run_db(_remove_extra_group, str(match['chat_id']))
            await update.message.reply_text(
                f"✅ *Group Removed.*\n\n"
                f"🏷️ Name: `{match['title']}`\n🆔 ID: `{match['chat_id']}`",
                parse_mode='Markdown',
            )
        msg = await _build_extra_groups_overview(context)
        await update.message.reply_text(
            msg, parse_mode='Markdown',
            reply_markup=get_extra_groups_keyboard(),
        )
        return

    # ── Channel Join — Add flow (username → auto-fetch) ──────────────────────
    if context.user_data.get('awaiting_ch_add_username'):
        context.user_data.pop('awaiting_ch_add_username')
        raw = text.strip().lstrip('@')
        ch_id = '@' + raw
        try:
            chat = await context.bot.get_chat(ch_id)
            uname    = chat.username or raw
            ch_name  = chat.title or uname
            ch_url   = f"https://t.me/{uname}"
            ch_id_db = '@' + uname
        except Exception as fetch_err:
            await update.message.reply_text(
                f"❌ *Could not fetch channel info.*\n\n"
                f"Error: `{fetch_err}`\n\n"
                "Make sure the bot is an admin in the channel and the username is correct.\n"
                "_Send /cancel to cancel._",
                parse_mode='Markdown',
                reply_markup=get_channel_join_keyboard(),
            )
            context.user_data['awaiting_ch_add_username'] = True
            return
        ok = await run_db(_add_required_channel, ch_name, ch_url, ch_id_db)
        if ok:
            _membership_cache.clear()
            channels = await run_db(_get_required_channels)
            interval = await run_db(_get_channel_check_interval)
            lines    = [f"`{c['name']}` — `{c['url']}`" for c in channels]
            await update.message.reply_text(
                f"✅ *Channel Added Successfully!*\n\n"
                f"🏷 Name: `{ch_name}`\n"
                f"🔗 Link: {ch_url}\n"
                f"🆔 ID: `{ch_id_db}`\n\n"
                f"📡 *All Channels:*\n" + "\n".join(lines) + f"\n\n"
                f"⏱ Check Interval: `{interval}` seconds",
                parse_mode='Markdown',
                reply_markup=get_channel_join_keyboard(),
            )
        else:
            await update.message.reply_text(
                f"⚠️ Channel `{ch_id_db}` is already in the list.",
                parse_mode='Markdown',
                reply_markup=get_channel_join_keyboard(),
            )
        return

    # ── Channel Join — Edit flow ───────────────────────────────────────────────
    if context.user_data.get('awaiting_ch_edit_index'):
        channels = await run_db(_get_required_channels)
        try:
            idx = int(text.strip()) - 1
            if idx < 0 or idx >= len(channels):
                raise ValueError
        except (ValueError, TypeError):
            await update.message.reply_text(
                f"❌ Invalid number. Please send a number between 1 and {len(channels)}.",
                reply_markup=get_channel_join_keyboard(),
            )
            return
        context.user_data.pop('awaiting_ch_edit_index')
        context.user_data['ch_edit_index']        = idx
        context.user_data['awaiting_ch_edit_name'] = True
        ch = channels[idx]
        await update.message.reply_text(
            f"✏️ Editing: *{ch['name']}*\n\n"
            "Step 1️⃣: Send the *new display name*.\n"
            f"_Current:_ `{ch['name']}`\n\n"
            "_Send /cancel to cancel._",
            parse_mode='Markdown',
            reply_markup=get_channel_join_keyboard(),
        )
        return

    if context.user_data.get('awaiting_ch_edit_name'):
        context.user_data.pop('awaiting_ch_edit_name')
        context.user_data['ch_edit_name']       = text.strip()
        context.user_data['awaiting_ch_edit_url'] = True
        await update.message.reply_text(
            f"✅ Name: *{text.strip()}*\n\n"
            "Step 2️⃣: Send the *new channel/group link*.\n\n"
            "_Send /cancel to cancel._",
            parse_mode='Markdown',
            reply_markup=get_channel_join_keyboard(),
        )
        return

    if context.user_data.get('awaiting_ch_edit_url'):
        context.user_data.pop('awaiting_ch_edit_url')
        idx     = context.user_data.pop('ch_edit_index', 0)
        ch_name = context.user_data.pop('ch_edit_name', '')
        ch_url  = text.strip()
        m       = re.match(r'https?://t\.me/([a-zA-Z0-9_]+)$', ch_url)
        ch_id   = ('@' + m.group(1)) if m else ch_url
        ok = await run_db(_update_required_channel, idx, ch_name, ch_url, ch_id)
        if ok:
            _membership_cache.clear()
            channels = await run_db(_get_required_channels)
            lines    = [f"`{c['name']}` — `{c['url']}`" for c in channels]
            await update.message.reply_text(
                f"✅ *Channel Updated Successfully!*\n\n"
                f"🏷 Name: `{ch_name}`\n"
                f"🔗 Link: {ch_url}\n"
                f"🆔 ID: `{ch_id}`\n\n"
                f"📡 *All Channels:*\n" + "\n".join(lines),
                parse_mode='Markdown',
                reply_markup=get_channel_join_keyboard(),
            )
        else:
            await update.message.reply_text(
                "❌ Update failed. Channel index may be invalid.",
                reply_markup=get_channel_join_keyboard(),
            )
        return

    # ── Channel Join — Delete flow (name-based) ───────────────────────────────
    if context.user_data.get('awaiting_ch_delete_name'):
        channels = await run_db(_get_required_channels)
        typed    = text.strip()
        match_idx = next(
            (i for i, c in enumerate(channels) if c['name'].lower() == typed.lower()),
            None,
        )
        if match_idx is None:
            names_list = "\n".join(f"`{c['name']}`" for c in channels)
            await update.message.reply_text(
                f"❌ Channel *{typed}* not found.\n\n"
                f"Available channels:\n{names_list}\n\n"
                "Type the exact name or send /cancel to cancel.",
                parse_mode='Markdown',
                reply_markup=get_channel_join_keyboard(),
            )
            return
        context.user_data.pop('awaiting_ch_delete_name')
        removed_name = channels[match_idx]['name']
        ok = await run_db(_delete_required_channel, match_idx)
        if ok:
            _membership_cache.clear()
            channels = await run_db(_get_required_channels)
            if channels:
                lines  = [f"`{c['name']}` — `{c['url']}`" for c in channels]
                remain = "\n".join(lines)
            else:
                remain = "_No channels configured._"
            await update.message.reply_text(
                f"🗑️ *Channel Deleted:* `{removed_name}`\n\n"
                f"📡 *Remaining Channels:*\n{remain}",
                parse_mode='Markdown',
                reply_markup=get_channel_join_keyboard(),
            )
        else:
            await update.message.reply_text(
                "❌ Delete failed. Please try again.",
                reply_markup=get_channel_join_keyboard(),
            )
        return

    # ── Channel Join — Check Interval flow ────────────────────────────────────
    if context.user_data.get('awaiting_ch_interval'):
        try:
            secs = int(text.strip())
            if secs < 10:
                raise ValueError
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Invalid value. Please send a number (minimum 10 seconds).",
                reply_markup=get_channel_join_keyboard(),
            )
            return
        context.user_data.pop('awaiting_ch_interval')
        await run_db(_set_channel_check_interval, secs)
        await update.message.reply_text(
            f"✅ *Check Interval Updated!*\n\n"
            f"⏱ New interval: *{secs} seconds*\n\n"
            "The membership enforcer will use this interval from the next cycle.",
            parse_mode='Markdown',
            reply_markup=get_channel_join_keyboard(),
        )
        return

    # ── Latest Message — robust top-level handler ─────────────────────────────
    # The Latest Message reply-keyboard button must always work, even if
    # panel_view_active was wiped (e.g. after a bot restart, since PTB keeps
    # user_data only in memory). Without this, the click falls through all
    # branches and hits the default `admin_start` at the bottom of handle_text,
    # which is what users perceive as "returning to the menu".
    if text == "📨 Latest Message":
        chat_id = update.effective_chat.id

        # Resolve the active panel, with progressively broader fallbacks.
        pname = (
            context.user_data.get('panel_view_active')
            or context.user_data.get('last_panel_view')
        )
        if not pname:
            await update.message.reply_text(
                "ℹ️ *Please select a panel first.*\n\n"
                "📋 *Panel List* → choose a panel → then press 📨 *Latest Message*.",
                parse_mode='Markdown')
            return

        # Restore panel context so subsequent buttons work normally.
        context.user_data['panel_view_active'] = pname
        context.user_data['last_panel_view']   = pname

        # Cancel any existing refresh task for this chat
        old_task = _refresh_tasks.pop(chat_id, None)
        if old_task and not old_task.done():
            old_task.cancel()

        # Fetch from monitor's in-memory record (updated every 3 s by background poller)
        rec = await asyncio.to_thread(get_panel_latest_today, pname)

        if not rec:
            await update.message.reply_text(
                f"📭 *{pname} — No SMS Today*\n\n"
                "No SMS messages have been received from this panel today.\n\n"
                "Panel may not be logged in or has no SMS yet.",
                parse_mode='Markdown')
            return

        text_msg = _format_panel_latest(rec, pname=pname)
        if len(text_msg) > 4000:
            text_msg = text_msg[:3990] + "\n…"

        try:
            sent_msg = await update.message.reply_text(
                text_msg,
                parse_mode='Markdown',
                reply_markup=_STOP_MARKUP,
            )
        except Exception:
            # Markdown parse failed (e.g. backtick in SMS body) — send plain text
            plain = text_msg.replace('`', "'").replace('*', '').replace('_', '').replace('[', '(')
            if len(plain) > 4000:
                plain = plain[:3990] + "\n…"
            sent_msg = await update.message.reply_text(plain, reply_markup=_STOP_MARKUP)

        # Start background auto-refresh task (edits the sent message every 3 s)
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            _refresh_latest_msg_loop(
                chat_id=chat_id,
                message_id=sent_msg.message_id,
                bot=context.bot,
                last_rec_id=rec.get('id', 0),
                pname=pname,
            )
        )
        _refresh_tasks[chat_id] = task
        return

    # ── Panel view action keyboard flow ───────────────────────────────────────
    if context.user_data.get('panel_view_active'):
        pname = context.user_data['panel_view_active']

        if text == "🔙 Back to Panel List":
            context.user_data.pop('panel_view_active', None)
            context.user_data.pop('panel_list_category', None)
            context.user_data.pop('panel_list_source', None)
            context.user_data.pop('panel_list_multiple_active', None)
            context.user_data['panel_list_active'] = True
            all_panels_db = await run_db(_get_panels)
            all_names = [pname for pname, _m in ALL_PANEL_LIST]
            panels = [p for p in all_panels_db if p['name'] in all_names]
            panels.sort(key=lambda p: all_names.index(p['name']) if p['name'] in all_names else 999)
            panel_btns = [KeyboardButton(_panel_label(p['name'])) for p in panels]
            rows = [panel_btns[i:i+2] for i in range(0, len(panel_btns), 2)]
            rows.append([KeyboardButton("🔙 Back to Admin Panel")])
            await update.message.reply_text(
                f"📋 *Panel List* ({len(panels)} panels)\n\nSelect a panel:",
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True))
            return

        if text == "change user/pass":
            pname = context.user_data.get('panel_view_active')
            if not pname:
                return
            context.user_data['awaiting_cred_panel']    = pname
            context.user_data.pop('awaiting_cred_username', None)
            await update.message.reply_text(
                f"🔑 *Change User/Pass — {_md_escape(pname)}*\n\n"
                f"Send the *new username* for this panel:\n\n"
                "_Send /cancel to cancel._",
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        if text == "📨 Latest Message":
            pname = context.user_data.get('panel_view_active')
            chat_id = update.effective_chat.id

            # Cancel any existing refresh task for this chat
            old_task = _refresh_tasks.pop(chat_id, None)
            if old_task and not old_task.done():
                old_task.cancel()

            # Fetch from monitor's in-memory record (updated every 3 s by background poller)
            rec = await asyncio.to_thread(get_panel_latest_today, pname)

            if not rec:
                await update.message.reply_text(
                    f"📭 *{_md_escape(pname)} — No SMS Today*\n\n"
                    "No SMS messages have been received from this panel today.\n\n"
                    "Panel may not be logged in or has no SMS yet.",
                    parse_mode='Markdown')
                return

            text_msg = _format_panel_latest(rec, pname=pname or "")
            if len(text_msg) > 4000:
                text_msg = text_msg[:3990] + "\n…"

            try:
                sent_msg = await update.message.reply_text(
                    text_msg,
                    parse_mode='Markdown',
                    reply_markup=_STOP_MARKUP,
                )
            except Exception:
                plain = text_msg.replace('`', "'").replace('*', '').replace('_', '').replace('[', '(')
                if len(plain) > 4000:
                    plain = plain[:3990] + "\n…"
                sent_msg = await update.message.reply_text(plain, reply_markup=_STOP_MARKUP)

            # Start background auto-refresh task (edits the sent message every 3 s)
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                _refresh_latest_msg_loop(
                    chat_id=chat_id,
                    message_id=sent_msg.message_id,
                    bot=context.bot,
                    last_rec_id=rec.get('id', 0),
                    pname=pname or "",
                )
            )
            _refresh_tasks[chat_id] = task
            return

        # Check if user clicked a panel button while panel_view was still active
        # (e.g. Lamix sms clicked while panel_view_active is stale)
        possible_panel_name = _panel_name_from_label(text)
        possible_panel = await run_db(_get_panel_by_name, possible_panel_name)
        if possible_panel:
            context.user_data['panel_view_active'] = possible_panel['name']
            context.user_data['last_panel_view']   = possible_panel['name']
            context.user_data['panel_list_active'] = True
            pname = possible_panel['name']
            panel_action_keyboard = ReplyKeyboardMarkup([
                [KeyboardButton("📨 Latest Message"), KeyboardButton("change user/pass")],
                [KeyboardButton("🔙 Back to Panel List")],
            ], resize_keyboard=True)
            db_user = _resolve_panel_user(possible_panel, pname)
            await update.message.reply_text(
                f"🖥️ *{_md_escape(pname)}*\n\n"
                f"👤 Username: `{db_user}`\n"
                f"🔗 URL: `{possible_panel['base_url']}`\n\n"
                "Choose an option:",
                parse_mode='Markdown',
                reply_markup=panel_action_keyboard)
            return

        return

    # ── Panel list mobile keyboard flow (ALL panels unified) ─────────────────
    if context.user_data.get('panel_list_active') or context.user_data.get('panel_list_multiple_active'):
        # If the admin is currently waiting for specific text input from another
        # feature, do NOT intercept — let the dedicated handler below process it.
        _SKIP_PANEL_LIST_STATES = (
            'awaiting_edit_bot_link', 'awaiting_broadcast_message',
            'awaiting_otp_bonus_amount', 'awaiting_otp_daily_limit',
            'awaiting_country_otp_bonus', 'awaiting_country_otp_bonus_amount',
            'awaiting_number_limit', 'awaiting_panel_interval',
            'awaiting_balance_user_id', 'awaiting_balance_amount',
            'awaiting_new_admin', 'awaiting_min_withdraw',
            'awaiting_new_country_name', 'awaiting_reset_country_name',
            'awaiting_add_numbers_country', 'awaiting_country_name',
            'awaiting_numbers_file', 'awaiting_admin_username',
            'awaiting_reset_users_confirm', 'awaiting_extra_group_id',
            'awaiting_extra_group_remove_id',
            'awaiting_ch_add_username', 'awaiting_ch_edit_index',
            'awaiting_ch_edit_name', 'awaiting_ch_edit_url',
            'awaiting_ch_delete_name', 'awaiting_ch_interval',
            'awaiting_svc_name',
            'panel_toggle_active',
            'awaiting_ref_bonus', 'awaiting_otp_daily_limit',
            'awaiting_withdraw_account', 'awaiting_withdraw_amount',
            'awaiting_withdraw_method',
            'awaiting_retry_login_panel', 'awaiting_reload_interval_panel',
            'awaiting_reload_interval_seconds', 'awaiting_session_cleanup_panel',
            'awaiting_panel_retry',
            'awaiting_delete_country_name', 'awaiting_specific_number_delete',
            'awaiting_cred_panel', 'awaiting_cred_username',
            'awaiting_user_info_id',
            'service_manager_active',
        )
        # Only intercept if NO awaiting-input state is active.
        # When admin is mid-flow (e.g. entering OTP bonus amount, broadcast
        # text, balance edit, etc.), skip this block entirely so the
        # dedicated handlers further below can process the input correctly.
        if not any(context.user_data.get(s) for s in _SKIP_PANEL_LIST_STATES):
            if text in ("🔙 Back", "🔙 Back to Panel List"):
                context.user_data.pop('panel_list_active', None)
                context.user_data.pop('panel_list_multiple_active', None)
                context.user_data.pop('panel_list_category', None)
                context.user_data.pop('panel_view_active', None)
                context.user_data.pop('panel_list_source', None)
                if _is_admin(username, user_id):
                    await update.message.reply_text(
                        "🔙 Back to Admin Panel.",
                        reply_markup=get_admin_keyboard())
                return
            # Treat text as a panel name (strip emoji prefix if present)
            panel_name_lookup = _panel_name_from_label(text)
            panel = await run_db(_get_panel_by_name, panel_name_lookup)
            if panel:
                pname = panel['name']
                context.user_data['panel_view_active'] = pname
                context.user_data['last_panel_view']   = pname
                context.user_data['panel_list_active'] = True
                context.user_data.pop('panel_list_multiple_active', None)
                panel_action_keyboard = ReplyKeyboardMarkup([
                    [KeyboardButton("📨 Latest Message"), KeyboardButton("change user/pass")],
                    [KeyboardButton("🔙 Back to Panel List")],
                ], resize_keyboard=True)
                db_user = _resolve_panel_user(panel, pname)
                await update.message.reply_text(
                    f"🖥️ *{_md_escape(pname)}*\n\n"
                    f"👤 Username: `{db_user}`\n"
                    f"🔗 URL: `{panel['base_url']}`\n\n"
                    "Choose an option:",
                    parse_mode='Markdown',
                    reply_markup=panel_action_keyboard)
            else:
                # Stale keyboard or unknown input — re-show all panels
                all_panels_db = await run_db(_get_panels)
                all_names = [pname for pname, _m in ALL_PANEL_LIST]
                panels = [p for p in all_panels_db if p['name'] in all_names]
                panels.sort(key=lambda p: all_names.index(p['name']) if p['name'] in all_names else 999)
                panel_btns = [KeyboardButton(_panel_label(p['name'])) for p in panels]
                rows = [panel_btns[i:i+2] for i in range(0, len(panel_btns), 2)]
                await update.message.reply_text(
                    f"📋 *Panel List* ({len(panels)} panels)\n\nSelect a panel:",
                    parse_mode='Markdown',
                    reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True))
            return
        # else: panel_list_active is set but an input-awaiting state is also
        # active — fall through to the dedicated handlers below.

    # ── Panel Toggle: admin typed (or copy-pasted) a panel name to toggle it ─
    if context.user_data.get('panel_toggle_active'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            context.user_data.pop('panel_toggle_active', None)
            return
        # Strip mono backticks (if admin copied the formatted text), surrounding
        # whitespace and any leading status emoji.
        raw = (text or "").strip()
        pname_typed = _panel_name_from_label(raw)
        match = None
        for pname, _m in ALL_PANEL_LIST:
            if pname.lower() == pname_typed.lower():
                match = pname
                break
        if not match:
            await update.message.reply_text(
                "❌ No panel found with that name.\n"
                "Copy the exact *name* from the list and try again.\n\n"
                "_(Send /cancel to cancel)_",
                parse_mode='Markdown')
            return
        # Toggle the panel
        currently_enabled = bool(await run_db(_is_panel_enabled, match))
        new_state = not currently_enabled
        await run_db(_set_panel_enabled, match, new_state)
        # Notify all admins (this also covers the requested toggle notification).
        await _notify_admins_panel_toggled(context.bot, match, new_state)
        # Clear the flag and return to the Admin Tools keyboard.
        context.user_data.pop('panel_toggle_active', None)
        action_word = "Enabled ✅" if new_state else "Disabled 🚫"
        await update.message.reply_text(
            f"🔌 *{_md_escape(match)}* is now {action_word}.\n\n"
            "Press 🔌 *Panel Toggle* again to toggle another panel.",
            parse_mode='Markdown',
            reply_markup=get_admin_tools_keyboard())
        return

    # ── Edit Panel Credentials: step 1 — admin typed new username ────────────
    if context.user_data.get('awaiting_cred_panel') and not context.user_data.get('awaiting_cred_username'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            context.user_data.pop('awaiting_cred_panel', None)
            return
        pname     = context.user_data['awaiting_cred_panel']
        new_uname = (text or "").strip()
        if not new_uname:
            await update.message.reply_text(
                "❌ Username cannot be empty. Send the new username:\n\n_Send /cancel to cancel._",
                parse_mode='Markdown')
            return
        context.user_data['awaiting_cred_username'] = new_uname
        await update.message.reply_text(
            f"✅ Username: `{new_uname}`\n\n"
            f"Now send the *new password* for *{_md_escape(pname)}*:\n\n"
            "_Send /cancel to cancel._",
            parse_mode='Markdown',
        )
        return

    # ── Edit Panel Credentials: step 2 — admin typed new password ────────────
    if context.user_data.get('awaiting_cred_panel') and context.user_data.get('awaiting_cred_username'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            context.user_data.pop('awaiting_cred_panel', None)
            context.user_data.pop('awaiting_cred_username', None)
            return
        pname     = context.user_data.pop('awaiting_cred_panel')
        new_uname = context.user_data.pop('awaiting_cred_username')
        new_pass  = (text or "").strip()
        if not new_pass:
            await update.message.reply_text(
                "❌ Password cannot be empty. Send the new password:\n\n_Send /cancel to cancel._",
                parse_mode='Markdown')
            context.user_data['awaiting_cred_panel']    = pname
            context.user_data['awaiting_cred_username'] = new_uname
            return
        # Save to DB
        ok = await run_db(_update_panel_credentials, pname, new_uname, new_pass)
        if not ok:
            await update.message.reply_text(f"❌ Panel *{_md_escape(pname)}* not found in database.", parse_mode='Markdown')
            return
        # Clear session on the matching monitor so it re-logins with new creds
        for mname, mon in ALL_PANEL_LIST:
            if mname == pname:
                try:
                    mon.logged_in = False
                    mon.session   = None
                except Exception:
                    pass
                break
        # Notify all admins
        try:
            all_admins = await run_db(_get_all_admins_with_details)
            notify_text = (
                f"✏️ *Panel Credentials Updated*\n\n"
                f"🖥️ *{_md_escape(pname)}*\n"
                f"👤 New Username: `{new_uname}`\n\n"
                f"🔄 Session cleared — panel will re-login automatically."
            )
            for adm in all_admins:
                adm_uid = adm.get("user_id")
                if adm_uid and adm_uid != user_id:
                    try:
                        await context.bot.send_message(chat_id=adm_uid, text=notify_text, parse_mode='Markdown')
                    except Exception:
                        pass
        except Exception:
            pass
        # Return to panel view
        panel = await run_db(_get_panel_by_name, pname)
        context.user_data['panel_list_active'] = True
        context.user_data['panel_view_active'] = pname
        panel_action_keyboard = ReplyKeyboardMarkup([
            [KeyboardButton("📨 Latest Message"), KeyboardButton("change user/pass")],
            [KeyboardButton("🔙 Back to Panel List")],
        ], resize_keyboard=True)
        await update.message.reply_text(
            f"✅ *Credentials updated successfully\\!*\n\n"
            f"🖥️ *{_md_escape(pname)}*\n"
            f"👤 Username: `{new_uname}`\n\n"
            f"🔄 Panel session cleared — will re\\-login with new credentials.",
            parse_mode='MarkdownV2',
            reply_markup=panel_action_keyboard,
        )
        return

    # ── Edit Bot Links: admin typed the new URL ───────────────────────────────
    if context.user_data.get('awaiting_edit_bot_link'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            context.user_data.pop('awaiting_edit_bot_link', None)
            return
        which = context.user_data.pop('awaiting_edit_bot_link')
        new_url = (text or "").strip()
        if not new_url.startswith("http"):
            await update.message.reply_text(
                "❌ Invalid link. Please send a link starting with `https://` or `http://`.\n"
                "_Send /cancel to cancel._",
                parse_mode='Markdown')
            context.user_data['awaiting_edit_bot_link'] = which
            return
        key_map = {
            "number":        "otp_btn_number",
            "channel_otp":   "otp_btn_channel",
            "support_group": "bot_link_support",
            "otp_group":     "otp_group_link",
        }
        label_map = {
            "number":        "📲 NUMBER",
            "channel_otp":   "📢 CHANNEL",
            "support_group": "👥 Support Group",
            "otp_group":     "📢 OTP Group",
        }
        await run_db(_set_setting, key_map[which], new_url)
        lnk_number   = await run_db(_get_setting, "otp_btn_number",   "https://t.me/UnofficialNumberBOT?start=start")
        lnk_channel  = await run_db(_get_setting, "otp_btn_channel",  "https://t.me/UnofficialNumber")
        lnk_support  = await run_db(_get_setting, "bot_link_support", SUPPORT_GROUP_LINK)
        lnk_otpgroup = await run_db(_get_setting, "otp_group_link",   "https://t.me/UnofficialNumber")
        await update.message.reply_text(
            f"✅ *{label_map[which]}* link updated!\n\n"
            f"🔗 New link: `{new_url}`\n\n"
            f"📋 *Current Links:*\n"
            f"📲 NUMBER: `{lnk_number}`\n"
            f"📢 CHANNEL: `{lnk_channel}`\n"
            f"👥 Support Group: `{lnk_support}`\n"
            f"📢 OTP Group: `{lnk_otpgroup}`",
            parse_mode='Markdown',
            reply_markup=get_edit_bot_links_keyboard())
        return

    # ── Service Manager: admin types text commands ───────────────────────────
    _OTHER_ACTIVE_STATES = (
        'awaiting_number_limit', 'awaiting_otp_bonus_amount', 'awaiting_otp_daily_limit',
        'awaiting_country_otp_bonus', 'awaiting_country_otp_bonus_amount',
        'awaiting_ref_bonus', 'awaiting_balance_user_id', 'awaiting_balance_amount',
        'awaiting_min_withdraw', 'awaiting_broadcast_message',
        'awaiting_new_country_name', 'awaiting_reset_country_name',
        'awaiting_add_numbers_country', 'awaiting_country_name',
        'awaiting_numbers_file', 'awaiting_admin_username',
        'awaiting_new_admin', 'awaiting_reset_users_confirm',
        'awaiting_extra_group_id', 'awaiting_extra_group_remove_id',
        'awaiting_edit_bot_link',
        'awaiting_ch_add_username', 'awaiting_ch_edit_index',
        'awaiting_ch_edit_name', 'awaiting_ch_edit_url',
        'awaiting_ch_delete_name', 'awaiting_ch_interval',
        'awaiting_cred_panel', 'awaiting_cred_username',
        'awaiting_retry_login_panel', 'awaiting_reload_interval_panel',
        'awaiting_reload_interval_seconds', 'awaiting_session_cleanup_panel',
        'awaiting_panel_retry', 'panel_toggle_active',
        'awaiting_delete_country_name', 'awaiting_specific_number_delete',
        'awaiting_user_info_id',
        'awaiting_withdraw_method', 'awaiting_withdraw_account', 'awaiting_withdraw_amount',
        'panel_list_active', 'panel_list_multiple_active',
    )
    if context.user_data.get('service_manager_active') and not any(
        context.user_data.get(s) for s in _OTHER_ACTIVE_STATES
    ):
        if not _is_admin(username, user_id):
            context.user_data.pop('service_manager_active', None)
            return
        cmd = text.strip()

        if cmd.lower().startswith("delete "):
            svc_name = cmd[7:].strip()
            ok = await run_db(_remove_global_service, svc_name)
            status = (f"✅ *Service Removed:* `{svc_name}`" if ok
                      else f"❌ Service `{svc_name}` not found.")

        elif cmd.lower().startswith("map "):
            rest = cmd[4:].strip()
            global_svcs = await run_db(_get_global_services)
            countries   = await run_db(_get_countries)
            # Match service name greedily from the known services list
            # so multi-word services like "Tik tok" are handled correctly.
            svc_name     = None
            country_input = None
            rest_lower = rest.lower()
            for svc in sorted(global_svcs, key=len, reverse=True):
                if rest_lower.startswith(svc.lower() + " "):
                    svc_name      = svc
                    country_input = rest[len(svc):].strip()
                    break
            if svc_name is None:
                # fallback: first word = service
                parts = rest.split(" ", 1)
                svc_name      = parts[0]
                country_input = parts[1] if len(parts) > 1 else ""
            if not country_input:
                await update.message.reply_text(
                    "❌ Format: `map ServiceName CountryName`\n"
                    "_Example:_ `map Tik tok Myanmar FB`",
                    parse_mode='Markdown',
                )
                return
            match_c = next(
                ((cid, cname) for cid, cname in countries
                 if cname.lower() == country_input.lower()), None
            )
            if not match_c:
                await update.message.reply_text(
                    f"❌ Country `{country_input}` not found. Check the name and try again.",
                    parse_mode='Markdown',
                )
                return
            real_svc = next(
                (s for s in global_svcs if s.lower() == svc_name.lower()), None
            )
            if not real_svc:
                await update.message.reply_text(
                    f"❌ Service `{svc_name}` not in the list. Add it first, then map.",
                    parse_mode='Markdown',
                )
                return
            cid, cname = match_c
            ok = await run_db(_add_country_service, cid, real_svc)
            status = (
                f"✅ *SERVICE ADDED*\n\n"
                f"🌍 Country: `{cname}`\n"
                f"🔧 Service: `{real_svc}`\n"
                f"🚀 Status: Mapped ✅\n\n"
                "_Country successfully added to service._"
            ) if ok else f"⚠️ `{real_svc}` is already mapped to `{cname}`."

        elif cmd.lower().startswith("unmap "):
            rest = cmd[6:].strip()
            global_svcs = await run_db(_get_global_services)
            countries   = await run_db(_get_countries)
            # Match service name greedily from the known services list
            svc_name      = None
            country_input = None
            rest_lower = rest.lower()
            for svc in sorted(global_svcs, key=len, reverse=True):
                if rest_lower.startswith(svc.lower() + " "):
                    svc_name      = svc
                    country_input = rest[len(svc):].strip()
                    break
            if svc_name is None:
                parts = rest.split(" ", 1)
                svc_name      = parts[0]
                country_input = parts[1] if len(parts) > 1 else ""
            if not country_input:
                await update.message.reply_text(
                    "❌ Format: `unmap ServiceName CountryName`\n"
                    "_Example:_ `unmap Tik tok Myanmar FB`",
                    parse_mode='Markdown',
                )
                return
            match_c = next(
                ((cid, cname) for cid, cname in countries
                 if cname.lower() == country_input.lower()), None
            )
            if not match_c:
                await update.message.reply_text(
                    f"❌ Country `{country_input}` not found.",
                    parse_mode='Markdown',
                )
                return
            cid, cname = match_c
            real_svc = next(
                (s for s in global_svcs if s.lower() == svc_name.lower()), svc_name
            )
            ok = await run_db(_unmap_service_from_country, real_svc, cid)
            status = (
                f"✅ *SERVICE REMOVED*\n\n"
                f"🌍 Country: `{cname}`\n"
                f"🔧 Service: `{real_svc}`\n"
                f"🚀 Status: Unmapped ✅"
            ) if ok else f"⚠️ `{real_svc}` was not mapped to `{cname}`."

        else:
            svc_name = cmd
            if not svc_name:
                return
            ok = await run_db(_add_global_service, svc_name)
            status = (f"✅ *Service Added:* `{svc_name}`" if ok
                      else f"⚠️ Service `{svc_name}` already exists.")

        await update.message.reply_text(status, parse_mode='Markdown')
        msg = await _build_service_manager_text()
        await update.message.reply_text(
            msg,
            parse_mode='Markdown',
            reply_markup=get_manage_numbers_keyboard(),
        )
        return

    # ── Broadcast: admin typed the message to send to all users ─────────────
    if context.user_data.get('awaiting_broadcast_message'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            context.user_data.pop('awaiting_broadcast_message', None)
            return
        context.user_data.pop('awaiting_broadcast_message', None)
        await _run_broadcast(update, context)
        return

    # ── Retry Login: admin typed a failed-panel name to manually re-login ────
    if context.user_data.get('awaiting_retry_login_panel'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            context.user_data.pop('awaiting_retry_login_panel', None)
            return
        typed = _panel_name_from_label((text or "").strip())
        match = None
        match_m = None
        for pname, m in ALL_PANEL_LIST:
            if pname.lower() == typed.lower():
                match = pname
                match_m = m
                break
        if not match:
            await update.message.reply_text(
                "❌ No panel found with that name.\n"
                "Copy the exact name from the list and try again.\n\n"
                "_(Send /cancel to cancel)_",
                parse_mode='Markdown')
            return
        # Verify the panel actually needs a retry (not already logged in)
        statuses = await run_db(_get_all_panel_statuses)
        s = next((x for x in (statuses or []) if x['panel_name'] == match), None)
        if s and s.get('logged_in'):
            context.user_data.pop('awaiting_retry_login_panel', None)
            await update.message.reply_text(
                f"ℹ️ *{match}* is already logged in successfully.",
                parse_mode='Markdown',
                reply_markup=get_admin_tools_keyboard())
            return

        await update.message.reply_text(
            f"⏳ Attempting login for *{match}*…",
            parse_mode='Markdown')

        def _do_retry_login(monitor):
            # Reset manual-only so the background loop will resume after success
            try:
                monitor._manual_only = False
            except Exception:
                pass
            try:
                ok = bool(monitor._login())
            except Exception:
                ok = False
            if ok and hasattr(monitor, '_extract_sesskey'):
                try:
                    monitor._extract_sesskey()
                except Exception:
                    pass
            return ok

        try:
            ok = await asyncio.to_thread(_do_retry_login, match_m)
        except Exception:
            ok = False

        try:
            if ok:
                await run_db(_update_panel_status, match, True, None, None)
            else:
                await run_db(_update_panel_status, match, False, None,
                             'Manual retry login failed')
        except Exception:
            pass

        if ok:
            # Notify ALL admins about the successful login (per requirement)
            try:
                await _notify_admins_login_success(context.bot, match)
            except Exception:
                pass
            # Refresh the failed-panels view so admin can pick the next one
            new_msg, has_failed = await _build_retry_login_view()
            if has_failed:
                context.user_data['awaiting_retry_login_panel'] = True
            else:
                context.user_data.pop('awaiting_retry_login_panel', None)
            await update.message.reply_text(
                f"✅ *{match}* logged in successfully.\n"
                f"_All admins have been notified._\n\n" + new_msg,
                parse_mode='Markdown',
                reply_markup=get_admin_tools_keyboard())
        else:
            # Keep the awaiting flag so the admin can try another panel
            await update.message.reply_text(
                f"❌ *{match}* login failed.\n"
                f"Try again later, or send another panel name.\n\n"
                f"_(Send /cancel to cancel)_",
                parse_mode='Markdown')
        return

    # ── Reload Interval flow: step 1 — admin typed a panel name ──────────────
    if context.user_data.get('awaiting_reload_interval_panel'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            context.user_data.pop('awaiting_reload_interval_panel', None)
            return
        typed = _panel_name_from_label((text or "").strip())
        match = None
        match_idx = None
        for idx, (pname, _m) in enumerate(ALL_PANEL_LIST):
            if pname.lower() == typed.lower():
                match = pname
                match_idx = idx
                break
        if not match:
            await update.message.reply_text(
                "❌ No panel with that name. Copy the exact name from the "
                "list and try again.\n\n_Send /cancel to abort._",
                parse_mode='Markdown')
            return
        cur = await run_db(_get_panel_interval, match)
        cur_secs = cur if cur is not None else 0
        cur_txt = f"{cur_secs}s" if cur_secs else "default"
        context.user_data.pop('awaiting_reload_interval_panel', None)
        context.user_data['awaiting_reload_interval_seconds'] = (match, match_idx)
        await update.message.reply_text(
            f"🔄 *Set Reload Interval — {match}*\n\n"
            f"Current interval: `{cur_txt}`\n\n"
            "Send the new interval in *seconds* (whole number), e.g. `30`.\n\n"
            "After every N seconds the bot will reload this panel and check "
            "for new SMS messages.\n\n"
            "_Send /cancel to abort._",
            parse_mode='Markdown')
        return

    # ── Reload Interval flow: step 2 — admin typed seconds ───────────────────
    if context.user_data.get('awaiting_reload_interval_seconds'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            context.user_data.pop('awaiting_reload_interval_seconds', None)
            return
        pname, idx = context.user_data['awaiting_reload_interval_seconds']
        try:
            seconds = int((text or "").strip())
            if seconds < 1:
                raise ValueError("too low")
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid value. Please send a whole number (seconds), "
                "e.g. `30`.\n\n_Send /cancel to abort._",
                parse_mode='Markdown')
            return
        context.user_data.pop('awaiting_reload_interval_seconds', None)
        # Persist to DB
        await run_db(_set_panel_interval, pname, seconds)
        # Apply live to the running monitor
        try:
            _, m = ALL_PANEL_LIST[idx]
            if hasattr(m, 'set_interval'):
                m.set_interval(seconds)
            else:
                m.interval = seconds
        except Exception:
            pass
        # Notify all admins
        try:
            from database import _get_all_admins_with_details
            admins = _get_all_admins_with_details()
            notify_text = (
                f"🔄 *Reload Interval Updated*\n\n"
                f"🖥️ *{pname}* will now reload every *{seconds}* second(s).\n"
                f"The change took effect immediately."
            )
            for admin in admins:
                uid = admin.get("user_id")
                if uid:
                    try:
                        await context.bot.send_message(
                            chat_id=uid, text=notify_text,
                            parse_mode='Markdown')
                    except Exception:
                        pass
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ *{pname}* reload interval set to `{seconds}s`.\n"
            f"_All admins have been notified._",
            parse_mode='Markdown',
            reply_markup=get_admin_tools_keyboard())
        return

    # ── Session Cleanup: admin sent a panel name ──────────────────────────────
    if context.user_data.get('awaiting_session_cleanup_panel'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            context.user_data.pop('awaiting_session_cleanup_panel', None)
            return
        # Match the typed name against known panels (case-insensitive, trimmed)
        typed = _panel_name_from_label((text or "").strip())
        match = None
        for pname, m in ALL_PANEL_LIST:
            if pname.lower() == typed.lower():
                match = (pname, m)
                break
        if not match:
            await update.message.reply_text(
                f"❌ *No panel found with name:* `{typed}`\n\n"
                "Copy the exact name from the list and try again, or press /cancel.",
                parse_mode='Markdown')
            return

        pname, m = match
        context.user_data.pop('awaiting_session_cleanup_panel', None)

        is_en = await run_db(_is_panel_enabled, pname)
        if not is_en:
            await update.message.reply_text(
                f"🚫 *{pname}* is currently disabled.",
                parse_mode='Markdown',
                reply_markup=get_admin_tools_keyboard())
            return

        await update.message.reply_text(
            f"⏳ Clearing session, cookies and sesskey for *{pname}*…",
            parse_mode='Markdown')

        def _do_session_clean(monitor):
            """Wipe session/cookies/sesskey/csrf etc. and put the monitor into
            manual-only mode so it does NOT auto re-login. The admin must use
            ⏱ Retry Interval to log it back in."""
            # Clear common session-related attributes on the monitor object
            for attr in ('session', 'sesskey', 'cookies', '_csrf', '_token',
                         'csrf_token', 'auth_token', '_session_id',
                         '_cookie_jar'):
                try:
                    if hasattr(monitor, attr):
                        setattr(monitor, attr, None)
                except Exception:
                    pass
            try:
                if hasattr(monitor, 'logged_in'):
                    monitor.logged_in = False
            except Exception:
                pass
            # Reset any cached "seen" set so first poll resyncs cleanly
            try:
                if hasattr(monitor, '_seen_keys'):
                    monitor._seen_keys.clear()
            except Exception:
                pass
            # Reset "first poll" so it doesn't spam after re-login
            try:
                if hasattr(monitor, '_is_first_poll'):
                    monitor._is_first_poll = True
            except Exception:
                pass
            # Put monitor in manual-only mode — no automatic login until admin
            # explicitly triggers it via ⏱ Retry Interval.
            try:
                monitor._manual_only = True
            except Exception:
                pass
            return True

        try:
            await asyncio.to_thread(_do_session_clean, m)
        except Exception:
            pass

        try:
            await run_db(_update_panel_status, pname, False, None,
                         'Session cleared — awaiting manual re-login')
        except Exception:
            pass

        await _notify_admins_session_cleaned(context.bot, pname)

        result_msg = (
            f"✅ *Session Cleanup Done*\n\n"
            f"🖥️ *{pname}*\n"
            f"  • Session: cleared\n"
            f"  • Cookie: cleared\n"
            f"  • Sesskey: cleared\n"
            f"  • Auto re-login: 🚫 disabled\n\n"
            f"To login again, go to ⏱ *Retry Interval* and send this panel's name.\n\n"
            f"_All admins have been notified._"
        )
        await update.message.reply_text(
            result_msg,
            parse_mode='Markdown',
            reply_markup=get_admin_tools_keyboard())
        return

    # ── Panel login-retry interval set flow ───────────────────────────────────
    if context.user_data.get('awaiting_panel_retry'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        pname, idx = context.user_data.pop('awaiting_panel_retry')
        raw = text.strip().lower()
        seconds = None
        try:
            if raw.endswith('h'):
                seconds = int(float(raw[:-1]) * 3600)
            elif raw.endswith('m'):
                seconds = int(float(raw[:-1]) * 60)
            elif raw.endswith('s'):
                seconds = int(float(raw[:-1]))
            else:
                seconds = int(raw)
            if seconds < 1:
                raise ValueError("too low")
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Invalid value. Example: `30` (seconds), `5m` (minutes), `2h` (hours).",
                parse_mode='Markdown')
            context.user_data['awaiting_panel_retry'] = (pname, idx)
            return
        await run_db(_set_panel_retry_interval, pname, seconds)
        # Apply live to the running monitor
        try:
            _, m = ALL_PANEL_LIST[idx]
            m.set_retry_interval(seconds)
        except Exception:
            pass
        if seconds >= 3600:
            human = f"{seconds // 3600}h"
        elif seconds >= 60:
            human = f"{seconds // 60}m"
        else:
            human = f"{seconds}s"
        await update.message.reply_text(
            f"✅ *{pname}* retry interval set to: `{human}` ({seconds}s).\n\n"
            "This panel will retry login every `" + human + "` after a failure. "
            "All admins will be notified as soon as login succeeds.",
            parse_mode='Markdown',
            reply_markup=get_admin_keyboard())
        return

    # ── Panel SMS-poll interval set flow ──────────────────────────────────────
    if context.user_data.get('awaiting_panel_interval'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        pname, idx = context.user_data.pop('awaiting_panel_interval')
        try:
            seconds = int(text.strip())
            if seconds < 1:
                raise ValueError("too low")
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid value. Please send a whole number (seconds), e.g. `30`.",
                parse_mode='Markdown')
            context.user_data['awaiting_panel_interval'] = (pname, idx)
            return
        await run_db(_set_panel_interval, pname, seconds)
        # Apply live to the running monitor
        try:
            _, m = ALL_PANEL_LIST[idx]
            m.set_interval(seconds)
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ *{pname}* polling interval updated to `{seconds}s`.\n\n"
            "The change takes effect immediately — no restart needed.",
            parse_mode='Markdown',
            reply_markup=get_admin_keyboard())
        return

    if context.user_data.get('awaiting_country_name'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        context.user_data['current_country_name']  = text
        context.user_data['awaiting_country_name'] = False
        context.user_data['awaiting_numbers_file'] = True
        await update.message.reply_text(
            f"✅ Country: *{text}*\n\nNow send a TXT file with phone numbers (one per line):",
            parse_mode='Markdown')
        return

    if context.user_data.get('awaiting_reset_country_name'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        raw = text.strip()
        if not raw.lower().startswith("reset "):
            await update.message.reply_text(
                "❌ Please send in format: `reset` <country name>\n"
                "Press any menu button to cancel.",
                parse_mode='Markdown')
            return
        target = raw[6:].strip()
        if not target:
            await update.message.reply_text(
                "❌ Please write the country name after `reset`.",
                parse_mode='Markdown')
            return
        countries = await run_db(_get_countries)
        found     = next(((r[0], r[1]) for r in countries
                          if r[1].lower() == target.lower()), None)
        if not found:
            await update.message.reply_text(
                f"❌ Country '{target}' not found. Try again or press a menu button.")
            return
        context.user_data.pop('awaiting_reset_country_name', None)
        cid, cname = found
        reset_count  = await run_db(_reset_country_numbers, cid)
        total, avail = await run_db(_get_numbers_count_by_country, cid)
        await update.message.reply_text(
            f"✅ *{cname}* reset complete.\n\n"
            f"🔄 {reset_count} numbers are now available again.\n"
            f"📊 Total: {total} | Available: {avail}",
            parse_mode='Markdown',
            reply_markup=get_manage_numbers_keyboard())
        return

    if context.user_data.get('awaiting_add_numbers_country'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        countries = await run_db(_get_countries)
        found     = next(((r[0], r[1]) for r in countries
                          if r[1].lower() == text.strip().lower()), None)
        if not found:
            await update.message.reply_text(
                f"❌ Country '{text}' not found. Enter the correct name or press a menu button.")
            return
        context.user_data.pop('awaiting_add_numbers_country', None)
        cid, cname = found
        context.user_data['edit_country_id']   = cid
        context.user_data['edit_country_name'] = cname
        await update.message.reply_text(
            f"✅ Country: *{cname}*\n\n"
            f"Now send a TXT file with phone numbers (one per line):",
            parse_mode='Markdown')
        return

    if context.user_data.get('awaiting_new_country_name'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        raw = text.strip()
        if not raw:
            await update.message.reply_text(
                "❌ Country name cannot be empty. Enter again or send /cancel.")
            return

        # Delete command: "delete <country name>"
        if raw.lower().startswith("delete "):
            target = raw[7:].strip()
            if not target:
                await update.message.reply_text(
                    "❌ Please write the country name after `delete`.\n"
                    "Example: `delete Bangladesh`",
                    parse_mode='Markdown')
                return
            countries = await run_db(_get_countries)
            found = next(
                ((r[0], r[1]) for r in countries if r[1].lower() == target.lower()),
                None)
            if not found:
                await update.message.reply_text(
                    f"❌ Country *{target}* not found. Check the name and try again.",
                    parse_mode='Markdown')
                return

            cid, cname = found
            total, _avail = await run_db(_get_numbers_count_by_country, cid)
            await run_db(_delete_country, cid)

            # Stay in the Add Country flow — show updated stats with delete notice
            context.user_data['awaiting_new_country_name'] = True
            countries_upd = await run_db(_get_countries)
            counts_upd    = await run_db(_get_all_country_counts)
            lines = [f"🗑️ *{cname}* deleted. ({total} numbers removed)", ""]
            lines += ["*🌐 Country Manager*", ""]
            if not countries_upd:
                lines.append("_No countries added yet._")
                lines.append("")
            else:
                grand_total = grand_avail = 0
                for cid2, cname2 in countries_upd:
                    total2, avail2 = counts_upd.get(cid2, (0, 0))
                    used2 = total2 - avail2
                    grand_total += total2
                    grand_avail += avail2
                    lines.append(
                        f"🌍 `{cname2}`\n"
                        f"  ➕ Added: `{total2}`  ✅ Available: `{avail2}`  🔴 Used: `{used2}`"
                    )
                    lines.append("`" + "─" * 30 + "`")
                grand_used = grand_total - grand_avail
                lines.append(
                    f"\n📌 *Total Countries:* `{len(countries_upd)}`\n"
                    f"🔢 *Total Numbers:* `{grand_total}`\n"
                    f"✅ *Available:* `{grand_avail}`  🔴 *Used:* `{grand_used}`"
                )
                lines.append("")
            lines.append("📝 Type a country name to add it.")
            lines.append("🗑️ To delete: type `delete` <country name>")
            lines.append("Send /cancel to cancel.")
            msg = "\n".join(lines)
            if len(msg) > 4000:
                msg = msg[:3990] + "\n`…`"
            await update.message.reply_text(msg, parse_mode='Markdown',
                                            reply_markup=get_manage_numbers_keyboard())

            # Notify all admins about the deletion
            try:
                admins = await run_db(_get_all_admins_with_details)
                admin_name = update.effective_user.full_name or username or str(user_id)
                notify_text = (
                    f"🗑️ *Country Deleted*\n\n"
                    f"🌍 Country: *{cname}*\n"
                    f"📞 Numbers removed: `{total}`\n"
                    f"👤 Deleted by: *{admin_name}*\n"
                    f"🕐 Time: {datetime.now().strftime('%d %b %Y, %I:%M %p')}"
                )
                for adm in admins:
                    adm_uid = adm.get('user_id')
                    if adm_uid and adm_uid != user_id:
                        try:
                            await context.bot.send_message(
                                chat_id=adm_uid,
                                text=notify_text,
                                parse_mode='Markdown')
                        except Exception:
                            pass
            except Exception:
                pass
            return

        cname = raw
        existing_id = await run_db(_get_country_id_by_name, cname)
        if existing_id:
            await update.message.reply_text(
                f"⚠️ Country *{cname}* already exists. Enter a different name or send /cancel.",
                parse_mode='Markdown')
            return

        await run_db(_add_country, cname)

        # Stay in the Add Country flow — show updated stats with success notice
        context.user_data['awaiting_new_country_name'] = True
        countries_upd = await run_db(_get_countries)
        counts_upd    = await run_db(_get_all_country_counts)
        lines = [f"✅ *{cname}* added successfully!", ""]
        lines += ["*🌐 Country Manager*", ""]
        if not countries_upd:
            lines.append("_No countries added yet._")
            lines.append("")
        else:
            grand_total = grand_avail = 0
            for cid2, cname2 in countries_upd:
                total2, avail2 = counts_upd.get(cid2, (0, 0))
                used2 = total2 - avail2
                grand_total += total2
                grand_avail += avail2
                lines.append(
                    f"🌍 `{cname2}`\n"
                    f"  ➕ Added: `{total2}`  ✅ Available: `{avail2}`  🔴 Used: `{used2}`"
                )
                lines.append("`" + "─" * 30 + "`")
            grand_used = grand_total - grand_avail
            lines.append(
                f"\n📌 *Total Countries:* `{len(countries_upd)}`\n"
                f"🔢 *Total Numbers:* `{grand_total}`\n"
                f"✅ *Available:* `{grand_avail}`  🔴 *Used:* `{grand_used}`"
            )
            lines.append("")
        lines.append("📝 Type a country name to add it.")
        lines.append("🗑️ To delete: type `delete` <country name>")
        lines.append("Send /cancel to cancel.")
        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3990] + "\n`…`"
        await update.message.reply_text(msg, parse_mode='Markdown',
                                        reply_markup=get_manage_numbers_keyboard())

        # Notify all admins about the new country
        try:
            admins = await run_db(_get_all_admins_with_details)
            admin_name = update.effective_user.full_name or username or str(user_id)
            notify_text = (
                f"🌍 *New Country Added*\n\n"
                f"✅ Country: *{cname}*\n"
                f"👤 Added by: *{admin_name}*\n"
                f"🕐 Time: {datetime.now().strftime('%d %b %Y, %I:%M %p')}"
            )
            for adm in admins:
                adm_uid = adm.get('user_id')
                if adm_uid and adm_uid != user_id:
                    try:
                        await context.bot.send_message(
                            chat_id=adm_uid,
                            text=notify_text,
                            parse_mode='Markdown')
                    except Exception:
                        pass
        except Exception:
            pass
        return

    if context.user_data.get('awaiting_delete_country_name'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        context.user_data['awaiting_delete_country_name'] = False
        countries = await run_db(_get_countries)
        found     = next(((r[0], r[1]) for r in countries
                          if r[1].lower() == text.lower()), None)
        if not found:
            await update.message.reply_text(
                f"❌ Country '{text}' not found. Check the name and try again.",
                reply_markup=get_manage_numbers_keyboard())
            return
        cid, cname = found
        deleted, removed = await run_db(_delete_country, cid)
        status_line = "🌍 Country: *Removed*" if removed else "🌍 Country: *Kept*"
        await update.message.reply_text(
            f"✅ *Deleted Successfully*\n\n"
            f"🌍 Country: *{cname}*\n"
            f"🗑️ Removed: *{deleted}* numbers\n"
            f"{status_line}",
            parse_mode='Markdown',
            reply_markup=get_manage_numbers_keyboard())
        return

    if context.user_data.get('awaiting_specific_number_delete'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        context.user_data['awaiting_specific_number_delete'] = False
        deleted = await run_db(_delete_number, text)
        if deleted:
            await update.message.reply_text(
                f"✅ Number `{text}` deleted successfully!",
                parse_mode='Markdown',
                reply_markup=get_manage_numbers_keyboard())
        else:
            await update.message.reply_text(
                f"❌ Number `{text}` not found!",
                parse_mode='Markdown',
                reply_markup=get_manage_numbers_keyboard())
        return

    if context.user_data.get('awaiting_new_admin'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        context.user_data['awaiting_new_admin'] = False
        uid_text = text.strip()
        if not uid_text.isdigit():
            await update.message.reply_text(
                "❌ *Invalid Input!*\n\nEnter only numbers (User ID). Example: `1234567890`",
                parse_mode='Markdown',
                reply_markup=get_manage_admins_keyboard()
            )
            return
        ok, row, reason = await run_db(_add_admin_by_uid, int(uid_text))
        if reason == "user_not_found":
            await update.message.reply_text(
                f"❌ *User Not Found!*\n\n"
                f"UID `{uid_text}` is not in the database.\n"
                f"Ask the user to send `/start` to the bot first.",
                parse_mode='Markdown',
                reply_markup=get_manage_admins_keyboard()
            )
        elif reason == "already_admin":
            fname = row.get('first_name') or ''
            await update.message.reply_text(
                f"⚠️ *Already an Admin!*\n\n"
                f"👤 Name: *{fname}*\n"
                f"🆔 UID: `{row.get('user_id')}`\n\n"
                f"This user is already an admin.",
                parse_mode='Markdown',
                reply_markup=get_manage_admins_keyboard()
            )
        else:
            fname = row.get('first_name') or ''
            lname = row.get('last_name') or ''
            full_name = f"{fname} {lname}".strip()
            await update.message.reply_text(
                f"✅ *Admin Added Successfully!*\n\n"
                f"┣━━━━━━━━━━━━━━━━━━━━━\n"
                f"┃ 👤 Name: *{full_name}*\n"
                f"┃ 🆔 UID: `{row.get('user_id')}`\n"
                f"┗━━━━━━━━━━━━━━━━━━━━━",
                parse_mode='Markdown',
                reply_markup=get_manage_admins_keyboard()
            )
        return

    # ── Min withdraw amount input ─────────────────────────────────────────────
    if context.user_data.get('awaiting_min_withdraw'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        try:
            amount = float(text.replace(',', '.').strip())
            if amount < 0:
                raise ValueError
            await run_db(_set_min_withdraw, amount)
            context.user_data['awaiting_min_withdraw'] = False
            await update.message.reply_text(
                f"✅ *Minimum Withdraw Updated!*\n\n"
                f"📤 From now on, the minimum withdraw is *৳ {amount:.2f}*.",
                parse_mode='Markdown',
                reply_markup=get_admin_keyboard()
            )
        except ValueError:
            await update.message.reply_text("❌ Enter a valid number. (Example: 50 or 100)")
        return

    # ── Withdraw: account number input ────────────────────────────────────────
    if context.user_data.get('awaiting_withdraw_account'):
        account = text.strip()
        method  = context.user_data.get('withdraw_method', '')
        context.user_data['awaiting_withdraw_account'] = False
        context.user_data['withdraw_account']          = account
        context.user_data['awaiting_withdraw_amount']  = True
        balance = await run_db(_get_user_balance, user_id)
        min_wd  = await run_db(_get_min_withdraw)
        await update.message.reply_text(
            f"💰 *Enter Withdraw Amount*\n\n"
            f"📱 Method: *{method}*\n"
            f"📞 Account: `{account}`\n"
            f"💵 Your balance: *৳ {balance:.2f}*\n"
            f"📤 Minimum: *৳ {min_wd:.2f}*\n\n"
            f"Enter how much you want to withdraw:",
            parse_mode='Markdown'
        )
        return

    # ── Withdraw: amount input ────────────────────────────────────────────────
    if context.user_data.get('awaiting_withdraw_amount'):
        method  = context.user_data.get('withdraw_method', '')
        account = context.user_data.get('withdraw_account', '')
        try:
            amount  = float(text.replace(',', '.').strip())
            balance = await run_db(_get_user_balance, user_id)
            min_wd  = await run_db(_get_min_withdraw)
            if amount < min_wd:
                await update.message.reply_text(
                    f"❌ Minimum withdraw amount is *৳ {min_wd:.2f}*. Enter a larger amount.",
                    parse_mode='Markdown'
                )
                return
            if amount > balance:
                await update.message.reply_text(
                    f"❌ Your balance is only *৳ {balance:.2f}*. You cannot withdraw that much.",
                    parse_mode='Markdown'
                )
                return
            context.user_data['awaiting_withdraw_amount'] = False
            context.user_data['withdraw_amount']          = amount
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm", callback_data="wd_confirm")],
                [InlineKeyboardButton("❌ Cancel",        callback_data="wd_cancel")],
            ])
            await update.message.reply_text(
                f"💸 *Withdraw Confirmation*\n\n"
                f"┣━━━━━━━━━━━━━━━━━━━━━\n"
                f"┃ 💰 Amount: *৳ {amount:.2f}*\n"
                f"┃ 📱 Method: *{method}*\n"
                f"┃ 📞 Account: `{account}`\n"
                f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Confirm?",
                parse_mode='Markdown',
                reply_markup=markup
            )
        except ValueError:
            await update.message.reply_text("❌ Enter a valid number.")
        return

    # ── Referral bonus amount input ───────────────────────────────────────────
    if context.user_data.get('awaiting_ref_bonus'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        try:
            amount = float(text.replace(',', '.').strip())
            if amount < 0:
                raise ValueError
            await run_db(_set_referral_bonus, amount)
            context.user_data['awaiting_ref_bonus'] = False
            await update.message.reply_text(
                f"✅ *Bonus Updated!*\n\n"
                f"💰 From now on, *৳ {amount:.2f}* bonus will be given per referral.",
                parse_mode='Markdown',
                reply_markup=get_admin_keyboard()
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Enter a valid number. (Example: 10 or 25.50)")
        return

    # ── Reset All Users confirmation ──────────────────────────────────────────
    if context.user_data.get('awaiting_reset_users_confirm'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        context.user_data['awaiting_reset_users_confirm'] = False
        if text.strip() == "YES DELETE":
            await update.message.reply_text(
                "⏳ *Creating backup...* Please wait.",
                parse_mode='Markdown'
            )
            try:
                zip_buf, stamp = await run_db(export_all_data_as_zip)
                from telegram import InputFile
                await context.bot.send_document(
                    chat_id=user_id,
                    document=InputFile(zip_buf, filename=f"backup_{stamp}.zip"),
                    caption=(
                        f"📦 *Full Data Backup*\n"
                        f"🗓️ Date: `{stamp}`\n\n"
                        f"This file contains:\n"
                        f"• User info and balances\n"
                        f"• Referral logs\n"
                        f"• Withdraw requests\n"
                        f"• OTP bonus logs\n"
                        f"• Number assignments & OTP deliveries\n"
                        f"• SMS logs"
                    ),
                    parse_mode='Markdown'
                )
            except Exception as e:
                await update.message.reply_text(
                    f"⚠️ Failed to create backup: `{e}`\n\nReset cancelled.",
                    parse_mode='Markdown',
                    reply_markup=get_settings_keyboard()
                )
                return

            summary          = await run_db(_reset_all_user_data, user_id)
            total_users      = summary.get('users', 0)
            total_referrals  = summary.get('referral_count', 0)
            referral_income  = summary.get('referral_income', 0.0)
            otp_income_today = summary.get('otp_income_today', 0.0)
            otp_income_total = summary.get('otp_income_total', 0.0)
            total_withdraws  = summary.get('withdraw_requests', 0)
            total_assignments = summary.get('number_assignments', 0)
            await update.message.reply_text(
                "✅ *Reset Complete!*\n\n"
                f"📦 Backup ZIP file sent ✓\n\n"
                f"👤 User accounts: *{total_users}* — *unchanged*\n"
                f"💰 All user balances → *reset to 0*\n\n"
                f"🗑️ Deleted:\n"
                f"  • Referral logs: *{total_referrals}*\n"
                f"  • Referral earnings: *৳ {referral_income:.2f}*\n"
                f"  • OTP earnings (today): *৳ {otp_income_today:.2f}*\n"
                f"  • OTP earnings (total): *৳ {otp_income_total:.2f}*\n"
                f"  • Withdraw requests: *{total_withdraws}*\n"
                f"  • Number assignments: *{total_assignments}*\n"
                f"  • OTP delivery tracking & SMS history\n\n"
                "🛡️ Admins, panels, countries, numbers and settings remain unchanged.",
                parse_mode='Markdown',
                reply_markup=get_settings_keyboard()
            )
        else:
            await update.message.reply_text(
                "❌ *Operation cancelled.*\n\nNo data was deleted.",
                parse_mode='Markdown',
                reply_markup=get_settings_keyboard()
            )
        return

    # ── Number Limit input ────────────────────────────────────────────────────
    if context.user_data.get('awaiting_number_limit'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        try:
            limit = int(text.strip())
            if limit < 1:
                raise ValueError
            await run_db(_set_number_limit, limit)
            context.user_data['awaiting_number_limit'] = False
            await update.message.reply_text(
                f"✅ *Number Limit Updated!*\n\n"
                f"🔢 From now on, each user will receive *{limit}* number(s) at a time.",
                parse_mode='Markdown',
                reply_markup=get_settings_keyboard()
            )
        except ValueError:
            await update.message.reply_text("❌ Enter a valid whole number. (Example: 1 or 3)")
        return

    # ── OTP Bonus amount input ────────────────────────────────────────────────
    if context.user_data.get('awaiting_otp_bonus_amount'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        try:
            amount = float(text.replace(',', '.').strip())
            if amount < 0:
                raise ValueError
            await run_db(_set_otp_bonus_amount, amount)
            context.user_data['awaiting_otp_bonus_amount'] = False
            await update.message.reply_text(
                f"✅ *OTP Bonus Updated!*\n\n"
                f"🎯 From now on, *৳ {amount:.2f}* bonus will be given per OTP notification.",
                parse_mode='Markdown',
                reply_markup=get_admin_keyboard()
            )
        except ValueError:
            await update.message.reply_text("❌ Enter a valid number. (Example: 2 or 5.50)")
        return

    # ── Country OTP Bonus input ───────────────────────────────────────────────
    if context.user_data.get('awaiting_country_otp_bonus') is not None and context.user_data.get('awaiting_country_otp_bonus') is not False:
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        cid   = context.user_data['awaiting_country_otp_bonus']
        cname = context.user_data.get('awaiting_country_otp_name', 'Unknown')
        try:
            amount = float(text.replace(',', '.').strip())
            if amount < 0:
                raise ValueError
            await run_db(_set_country_otp_bonus, cid, amount)
            context.user_data.pop('awaiting_country_otp_bonus', None)
            context.user_data.pop('awaiting_country_otp_name', None)
            await update.message.reply_text(
                f"✅ *{cname}* — OTP Bonus Updated!\n\n"
                f"🎯 From now on, *৳ {amount:.2f}* bonus will be given when an OTP is received on this country's number.",
                parse_mode='Markdown',
                reply_markup=get_settings_keyboard()
            )
        except ValueError:
            await update.message.reply_text("❌ Enter a valid number. (Example: 3 or 5.50)")
        return

    # ── User Info lookup ───────────────────────────────────────────────────────
    if context.user_data.get('awaiting_user_info_id'):
        if not _is_admin(username, user_id):
            context.user_data.pop('awaiting_user_info_id', None)
            return
        try:
            target_id = int(text.strip())
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid ID. Please enter a numeric Telegram User ID.",
                reply_markup=get_users_keyboard())
            return
        context.user_data.pop('awaiting_user_info_id', None)
        info = await run_db(_get_user_info_by_id, target_id)
        if not info:
            await update.message.reply_text(
                f"❌ No user found with ID `{target_id}`.",
                parse_mode='Markdown',
                reply_markup=get_users_keyboard())
            return
        otp_stats   = await run_db(_get_user_otp_bonus_stats, target_id)
        ref_count   = await run_db(_get_referral_count, target_id)
        ref_earned  = await run_db(_get_referral_total_earned, target_id)
        ref_code    = await run_db(_get_user_referral_code, target_id)
        otp_total   = otp_stats.get('total_count', 0) if otp_stats else 0
        otp_today   = otp_stats.get('today_count', 0) if otp_stats else 0
        uname_str   = f"@{info['username']}" if info.get('username') else "—"
        fname_str   = info.get('first_name') or "—"
        msg = (
            f"🔍 *User Info*\n\n"
            f"`{'─'*30}`\n"
            f"`👤 Name         : {fname_str}`\n"
            f"`🔗 Username     : {uname_str}`\n"
            f"`💎 User ID      : {info['user_id']}`\n"
            f"`{'─'*30}`\n"
            f"`💰 Balance      : {info['balance']:.2f} ৳`\n"
            f"`📨 OTP Total    : {otp_total}`\n"
            f"`📨 OTP Today    : {otp_today}`\n"
            f"`👥 Referrals    : {ref_count}`\n"
            f"`💵 Ref Earned   : {ref_earned:.2f} ৳`\n"
            f"`🎫 Ref Code     : {ref_code or '—'}`\n"
            f"`{'─'*30}`"
        )
        await update.message.reply_text(msg, parse_mode='Markdown',
                                        reply_markup=get_users_keyboard())
        return

    # ── Balance edit: get user ID ──────────────────────────────────────────────
    if context.user_data.get('awaiting_balance_user_id'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        try:
            target_id = int(text.strip())
            info = await run_db(_get_user_info_by_id, target_id)
            if not info:
                await update.message.reply_text("❌ No user found with this ID.")
                return
            context.user_data['awaiting_balance_user_id']  = False
            context.user_data['balance_edit_target_id']    = target_id
            context.user_data['awaiting_balance_amount']   = True
            name = f"@{info['username']}" if info['username'] else info['first_name'] or str(target_id)
            await update.message.reply_text(
                f"👤 User found: *{name}*\n"
                f"💰 Current balance: *৳ {info['balance']:.2f}*\n\n"
                f"Enter the new balance (use + to add, - to deduct, or a plain number to set):\n"
                f"_(Example: +50 or -10 or 100)_",
                parse_mode='Markdown'
            )
        except ValueError:
            await update.message.reply_text("❌ Enter a valid User ID (numbers only).")
        return

    # ── Balance edit: get amount ───────────────────────────────────────────────
    if context.user_data.get('awaiting_balance_amount'):
        if not _is_admin(username, user_id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        target_id = context.user_data.get('balance_edit_target_id')
        try:
            txt = text.strip()
            if txt.startswith('+'):
                amount = float(txt[1:].replace(',', '.'))
                await run_db(_update_user_balance, target_id, amount)
                action = f"*+৳ {amount:.2f}* added"
            elif txt.startswith('-'):
                amount = float(txt[1:].replace(',', '.'))
                await run_db(_update_user_balance, target_id, -amount)
                action = f"*-৳ {amount:.2f}* deducted"
            else:
                amount = float(txt.replace(',', '.'))
                await run_db(_set_user_balance, target_id, amount)
                action = f"*৳ {amount:.2f}* set"
            context.user_data['awaiting_balance_amount']  = False
            context.user_data.pop('balance_edit_target_id', None)
            new_balance = await run_db(_get_user_balance, target_id)
            await update.message.reply_text(
                f"✅ *Balance Updated Successfully!*\n\n"
                f"👤 User ID: `{target_id}`\n"
                f"🔧 Change: {action}\n"
                f"💰 New Balance: *৳ {new_balance:.2f}*",
                parse_mode='Markdown',
                reply_markup=get_admin_keyboard()
            )
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=(
                        f"💰 *Your Balance Has Been Updated!*\n\n"
                        f"Change: {action}\n"
                        f"New Balance: *৳ {new_balance:.2f}*"
                    ),
                    parse_mode='Markdown'
                )
            except Exception:
                pass
        except ValueError:
            await update.message.reply_text("❌ Enter a valid number. (Example: +50 or -10 or 100)")
        return

    # Default: show appropriate panel
    if _is_admin(username, user_id):
        await admin_start(update, context)
    else:
        await show_main_menu(update, context)


# ── Error handler ─────────────────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import traceback
    from telegram.error import Conflict, NetworkError, TimedOut, RetryAfter

    # Transient / expected errors — log as WARNING only, don't notify user
    if isinstance(context.error, Conflict):
        logger.warning("Telegram Conflict: closing old bot session — will recover shortly.")
        return
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning(f"Network error (transient): {context.error}")
        return
    if isinstance(context.error, RetryAfter):
        logger.warning(f"Rate limit — retrying after {context.error.retry_after}s.")
        return

    err_text = "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__))
    logger.error(f"Exception while handling an update:\n{err_text}")
    # Only reply to real user messages — never reply to channel posts (effective_user is None there)
    if update and update.effective_message and update.effective_user:
        try:
            await update.effective_message.reply_text(
                f"❌ *An error occurred!*\n\n`{context.error}`",
                parse_mode='Markdown'
            )
        except Exception:
            pass


# ── Post-init: start OTP monitor ──────────────────────────────────────────────

async def _periodic_membership_check_loop(bot):
    """
    Periodically checks ALL registered users to verify they are still members
    of every required channel.  The check interval is loaded live from the DB
    each cycle (minimum 10 s).  Non-members are notified (at most once per hour)
    and their cache is set to expire in 30 s so the next interaction re-checks
    them quickly after they rejoin.
    """
    from telegram.error import BadRequest, Forbidden, TelegramError

    async def _raw_check(user_id: int, channels: list) -> bool:
        """Fresh membership check — bypasses cache. Fails CLOSED (mandatory channels)."""
        async def _one(ch: dict) -> bool:
            try:
                m = await bot.get_chat_member(chat_id=ch['id'], user_id=user_id)
                return m.status not in ('left', 'kicked', 'restricted')
            except BadRequest as e:
                err = str(e).lower()
                if any(x in err for x in (
                    'user not found', 'not found', 'participant', 'not a member',
                    'member list is inaccessible', 'chat_admin_required',
                    'need to be invited', 'bot is not a member',
                )):
                    if ('inaccessible' in err or 'admin_required' in err):
                        if ch['id'] not in _channel_admin_warned:
                            _channel_admin_warned.add(ch['id'])
                            logger.warning(
                                f"[MemberEnforcer] ch={ch['id']} — Bot is NOT admin! "
                                "Add bot as Admin to this channel so membership checks work."
                            )
                    return False  # fail closed — cannot verify = treat as not member
                logger.warning(f"[MemberEnforcer] ch={ch['id']} BadRequest: {e}")
                return False  # fail closed
            except (Forbidden, TelegramError) as e:
                logger.warning(f"[MemberEnforcer] ch={ch['id']} error: {e} — add bot as Admin!")
                return False  # fail closed
            except Exception:
                return False  # fail closed
        results = await asyncio.gather(*[_one(ch) for ch in channels])
        return all(results)

    # Small initial delay so the bot fully starts before the first sweep
    await asyncio.sleep(5)

    while True:
        try:
            channels = await run_db(_get_required_channels)
            if not channels:
                interval = await run_db(_get_channel_check_interval)
                await asyncio.sleep(max(10, interval))
                continue

            all_users = await run_db(_get_all_users)
            if not all_users:
                interval = await run_db(_get_channel_check_interval)
                await asyncio.sleep(max(10, interval))
                continue

            total = len(all_users)
            now   = time.monotonic()
            checked = kicked = notified = 0

            # ── Check EVERY registered user (no batching) ─────────────────────
            for user_id in all_users:
                try:
                    if _is_admin(None, user_id):
                        continue

                    is_member = await _raw_check(user_id, channels)
                    checked  += 1

                    # Update cache: members 5 min, non-members 30 s (fast verify)
                    loop_time = asyncio.get_running_loop().time()
                    ttl = _MEMBERSHIP_CACHE_TTL if is_member else _MEMBERSHIP_CACHE_TTL_FAIL
                    _membership_cache[user_id] = (is_member, loop_time + ttl)

                    if not is_member:
                        kicked += 1
                        last_notified = _notified_recently.get(user_id, 0)
                        if (now - last_notified) >= _NOTIFY_COOLDOWN:
                            _notified_recently[user_id] = now
                            notified += 1
                            ch_count = len(channels)
                            keyboard = InlineKeyboardMarkup(
                                [[InlineKeyboardButton(f"📢 {ch['name']} — Join Now", url=ch['url'])]
                                 for ch in channels] +
                                [[InlineKeyboardButton("✅ Verify — I Joined All", callback_data="check_join")]]
                            )
                            try:
                                await bot.send_message(
                                    chat_id=user_id,
                                    text=(
                                        "🔒 *বট ব্যবহার করতে চ্যানেলে জয়েন করুন!*\n\n"
                                        f"আপনি নিচের *{ch_count}টি চ্যানেলের* একটি বা একাধিক "
                                        "ছেড়ে দিয়েছেন।\n"
                                        "জয়েন না করলে বট ব্যবহার করা যাবে না।\n\n"
                                        "👇 সব চ্যানেলে জয়েন করুন, তারপর *Verify* চাপুন:"
                                    ),
                                    parse_mode='Markdown',
                                    reply_markup=keyboard,
                                )
                            except Exception as send_err:
                                err_s = str(send_err).lower()
                                if not any(x in err_s for x in (
                                    'blocked', 'deactivated', 'not found', 'kicked'
                                )):
                                    logger.warning(
                                        f"[MemberEnforcer] notify {user_id} failed: {send_err}"
                                    )

                    await asyncio.sleep(0.05)   # 20 checks/sec — safe for Telegram API

                except Exception as user_err:
                    logger.debug(f"[MemberEnforcer] user {user_id} error: {user_err}")

            if checked:
                logger.info(
                    f"[MemberEnforcer] sweep done — checked={checked}/{total} "
                    f"not_member={kicked} notified={notified}"
                )

            # ── Sleep until the next sweep ─────────────────────────────────────
            interval = await run_db(_get_channel_check_interval)
            await asyncio.sleep(max(10, interval))

        except asyncio.CancelledError:
            break
        except Exception as loop_err:
            logger.error(f"[MemberEnforcer] loop error: {loop_err}")
            await asyncio.sleep(10)


async def _weekly_top_user_bonus_loop(bot):
    """Every Thursday 00:00 Bangladesh time (UTC+6) — give ৳100 bonus to #1 top user."""
    from datetime import timezone, timedelta as _td
    BD_TZ = timezone(_td(hours=6))
    BONUS = 100.0

    while True:
        try:
            now = datetime.now(BD_TZ)
            # weekday(): Monday=0 … Thursday=3
            days_ahead = (3 - now.weekday()) % 7
            if days_ahead == 0 and (now.hour > 0 or now.minute > 0 or now.second > 0):
                days_ahead = 7
            next_thursday = (
                now.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=days_ahead)
            )
            sleep_secs = (next_thursday - now).total_seconds()
            logger.info(
                f"[WeeklyBonus] Next bonus: {next_thursday.strftime('%Y-%m-%d %H:%M')} BD "
                f"(in {sleep_secs/3600:.1f}h)"
            )
            await asyncio.sleep(sleep_secs)

            top = await run_db(_get_top_users_detailed, 1)
            if not top:
                await asyncio.sleep(60)
                continue
            winner = top[0]
            uid  = winner['user_id']
            name = winner['display_name'] or f"ID:{uid}"
            await run_db(_update_user_balance, uid, BONUS)
            new_bal = await run_db(_get_user_balance, uid)
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=(
                        f"🏆 *Weekly Top User Bonus!*\n\n"
                        f"Congratulations *{name}*! 🎉\n\n"
                        f"You are the *#1 Top User* this week!\n"
                        f"💰 *+৳{BONUS:.2f}* bonus has been added to your balance.\n\n"
                        f"💎 New Balance: *৳ {new_bal:.2f}*\n\n"
                        f"Keep receiving OTP messages to stay on top! 🚀"
                    ),
                    parse_mode='Markdown',
                )
            except Exception as e:
                logger.warning(f"[WeeklyBonus] Could not notify user {uid}: {e}")
            logger.info(f"[WeeklyBonus] ৳{BONUS:.0f} bonus given to {name} (ID:{uid})")

            await asyncio.sleep(60)   # prevent double-fire within same minute

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[WeeklyBonus] loop error: {e}")
            await asyncio.sleep(60)


async def post_init(application: Application):
    # Force-close any existing getUpdates session from a previous instance.
    # This prevents the "Conflict" error when restarting the bot.
    try:
        await application.bot.get_updates(offset=-1, timeout=1)
    except Exception:
        pass
    logger.info("[Startup] Previous Telegram session cleared.")

    # Give the bot handler pool 500 threads (handles 20k+ concurrent users).
    # OTP monitors run in their own 24-thread pool (otp_monitor._OTP_EXECUTOR)
    # so the two pools never compete.
    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        concurrent.futures.ThreadPoolExecutor(
            max_workers=500,
            thread_name_prefix="bot-worker",
        )
    )

    # Create the message queue inside the running event loop (avoids cross-loop errors)
    global _msg_queue
    _msg_queue = asyncio.Queue()

    # Start Message Queue worker (rate-limited Telegram sender)
    loop.create_task(_message_queue_worker(), name="msg-queue-worker")
    logger.info("[MsgQueue] Message queue worker started (max %d msg/sec)", _MSG_RATE)

    # Start Memory Cleanup loop (runs every 30 minutes)
    loop.create_task(_memory_cleanup_loop(), name="memory-cleanup")
    logger.info("[MemCleanup] Memory cleanup task started (interval=%ds)", _CLEANUP_INTERVAL)

    # Start Periodic Membership Enforcer (checks ALL users each cycle, notifies non-members)
    loop.create_task(_periodic_membership_check_loop(application.bot), name="member-enforcer")
    logger.info("[MemberEnforcer] Periodic membership check started (all-users sweep, min 10s interval)")

    # Start Weekly Top-User Bonus loop (every Thursday 00:00 Bangladesh time)
    loop.create_task(_weekly_top_user_bonus_loop(application.bot), name="weekly-top-bonus")
    logger.info("[WeeklyBonus] Weekly top-user bonus task started (every Thursday 00:00 BD time)")

    # ── Startup: check bot has admin rights in required channels ──────────────
    async def _warn_channel_admin():
        """Send admin a one-time warning if bot lacks admin rights in any channel."""
        from telegram.error import BadRequest, Forbidden
        await asyncio.sleep(8)   # wait for bot to fully initialise
        try:
            channels = await run_db(_get_required_channels)
            if not channels:
                return
            missing = []
            for ch in channels:
                try:
                    me = await application.bot.get_me()
                    member = await application.bot.get_chat_member(
                        chat_id=ch['id'], user_id=me.id
                    )
                    if member.status not in ('administrator', 'creator'):
                        missing.append(ch['name'])
                except Exception:
                    missing.append(ch['name'])
            if missing:
                ch_list = '\n'.join(f"  • {n}" for n in missing)
                admin_ids = await run_db(_get_all_admins)
                warn_text = (
                    "⚠️ *Channel Admin Warning*\n\n"
                    "Bot is NOT an admin in the following required channels:\n\n"
                    f"{ch_list}\n\n"
                    "👉 Please add the bot as *Admin* in these channels so that "
                    "membership checks work correctly."
                )
                for admin_id in (admin_ids or []):
                    try:
                        await application.bot.send_message(
                            chat_id=admin_id,
                            text=warn_text,
                            parse_mode='Markdown',
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"[StartupCheck] Channel admin check failed: {e}")

    loop.create_task(_warn_channel_admin(), name="channel-admin-check")

    def _apply_saved_intervals():
        """Load stored polling & retry intervals from DB and apply to each monitor."""
        for pname, m in ALL_PANEL_LIST:
            try:
                saved = _get_panel_interval(pname)
                if saved:
                    m.set_interval(saved)
            except Exception:
                pass
            try:
                saved_retry = _get_panel_retry_interval(pname)
                if saved_retry:
                    m.set_retry_interval(saved_retry)
            except Exception:
                pass

    _apply_saved_intervals()

    async def _staggered_start():
        """Start monitors one by one with short delays to prevent concurrent
        login failures caused by panel-side rate-limiting / single-session
        restrictions."""
        bot = application.bot
        monitor.start(bot)
        await asyncio.sleep(4)
        msi_sms_monitor.start(bot)
        await asyncio.sleep(4)
        proof_sms_monitor.start(bot)
        await asyncio.sleep(4)
        lamix_sms_monitor.start(bot)
        await asyncio.sleep(4)
        purple_sms_monitor.start(bot)
        await asyncio.sleep(4)
        seven1tel_monitor.start(bot)
        await asyncio.sleep(4)
        mait_sms_monitor.start(bot)
        await asyncio.sleep(4)
        zento_sms_monitor.start(bot)
        await asyncio.sleep(4)
        wolf_sms_monitor.start(bot)
        await asyncio.sleep(4)
        shark_sms_monitor.start(bot)
        await asyncio.sleep(4)
        sms_hadi2_monitor.start(bot)
        await asyncio.sleep(4)
        konekta_monitor.start(bot)     # known to fail if started too early
        await asyncio.sleep(6)
        number_panel_monitor.start(bot)  # known to fail if started too early

    loop.create_task(_staggered_start())


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    _init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .connection_pool_size(128)   # HTTP connections to Telegram API
        .connect_timeout(20.0)
        .read_timeout(20.0)
        .write_timeout(20.0)
        .pool_timeout(10.0)
        .post_init(post_init)
        .build()
    )

    # Admin keyboard buttons
    application.add_handler(MessageHandler(
        filters.Text([
            "🌍 𝑪𝒐𝒖𝒏𝒕𝒓𝒚 𝑴𝒂𝒏𝒂𝒈𝒆𝒓", "👤 Manage Admins", "🔙 Back to Admin Panel",
            "👥 Add Admin",     "🔧 Remove Admin",
            "👥 Users", "👤 User Count", "📈 User Stats", "🔍 User Info",
            "🔄 𝑹𝒆𝒔𝒆𝒕 𝑵𝒖𝒎𝒃𝒆𝒓",
            "📋 Panel List",
            "⚙️ Settings",
            "🛠 Admin Tools",
            "📢 Extra Groups",
            "➕ Add Group", "🗑️ Remove Group",
            "⏱ Retry Interval",
            "🧹 Session Cleanup",
            "🔌 Panel Toggle",
            "🔄 Reload Interval",
            "📢 Broadcast",
            "🚀 Force Start",
            "🔗 Edit Bot Links",
            "📲 NUMBER Link",
            "📢 CHANNEL Link",

            "🎁 Referral Settings", "🎁 Referral",
            "🎯 OTP Bonus Settings", "🎯 OTP Bonus",
            "🔢 Number Limit",
            "🌍 Country OTP Bonus",
            "🗑️ Reset All Users",
            "🌐Add 𝑪𝒐𝒖𝒏𝒕𝒓𝒚",
            "📲 𝑨𝒅𝒅 𝑵𝒖𝒎𝒃𝒆𝒓",
            "🛠️ 𝑺𝒆𝒓𝒗𝒊𝒄𝒆𝒔",
            # OTP Bonus sub-menu
            "🔛 OTP Bonus Toggle", "💰 Set Bonus Amount",
            # Referral sub-menu
            "🔛 Referral Toggle", "💰 Set Referral Bonus",
            "📤 Set Min Withdraw", "💸 Pending Withdraws",
            # Shared
            "👤 Edit Balance", "🔙 Back to Settings",
            "📊 𝑩𝒐𝒕 𝑺𝒕𝒂𝒕𝒊𝒔𝒕𝒊𝒄𝒔",
            # Channel Join Requirement
            "📡 Channel Join",
            "➕ Add Channel", "✏️ Edit Channel", "🗑️ Delete Channel", "🕑 Check Interval",
            "🔙 Back to Admin Tools",
        ]),
        handle_button_click,
    ))

    # User keyboard buttons
    application.add_handler(MessageHandler(
        filters.Text([
            "☎️ Get Number", "Get Numbers",
            "🌍 Available Country",
            "👥 Support Group",
            "💰 My Balance",
            "💸 Withdraw",
            "🏆 Top Users",
        ]),
        handle_user_button_click,
    ))

    application.add_handler(CommandHandler("start",  start_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    application.add_error_handler(error_handler)

    print("🤖 Bot is running…")
    # Exclude channel_post / edited_channel_post — the bot is admin in some channels
    # and those updates have effective_user=None which caused spurious errors.
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=[
            "message",
            "edited_message",
            "callback_query",
            "inline_query",
            "chosen_inline_result",
            "my_chat_member",
            "chat_member",
        ],
    )


if __name__ == '__main__':
    main()
