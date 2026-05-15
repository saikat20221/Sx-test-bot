from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, CopyTextButton,
)


ADMIN_BUTTON_LAYOUT = [
    ["🌍 𝑪𝒐𝒖𝒏𝒕𝒓𝒚 𝑴𝒂𝒏𝒂𝒈𝒆𝒓", "👤 Manage Admins"],
    ["👥 Users",          "📋 Panel List"],
    ["📢 Broadcast",      "⚙️ Settings"],
    ["📊 𝑩𝒐𝒕 𝑺𝒕𝒂𝒕𝒊𝒔𝒕𝒊𝒄𝒔", "☎️ Get Number"],
]


def get_admin_keyboard():
    rows = []
    for layout_row in ADMIN_BUTTON_LAYOUT:
        rows.append([KeyboardButton(b) for b in layout_row])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_edit_bot_links_keyboard():
    keyboard = [
        [KeyboardButton("📲 NUMBER Link"),       KeyboardButton("📢 CHANNEL Link")],
        [KeyboardButton("👥 Support Group Link"), KeyboardButton("📢 OTP Group Link")],
        [KeyboardButton("🔙 Back to Admin Tools")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_extra_groups_keyboard():
    keyboard = [
        [KeyboardButton("➕ Add Group"),  KeyboardButton("🗑️ Remove Group")],
        [KeyboardButton("🔙 Back to Admin Tools")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_admin_tools_keyboard():
    keyboard = [
        [KeyboardButton("🔌 Panel Toggle"),    KeyboardButton("🚀 Force Start")],
        [KeyboardButton("🧹 Session Cleanup"), KeyboardButton("⏱ Retry Interval")],
        [KeyboardButton("🔄 Reload Interval"), KeyboardButton("📢 Extra Groups")],
        [KeyboardButton("🔗 Edit Bot Links"),  KeyboardButton("📡 Channel Join")],
        [KeyboardButton("🔙 Back to Admin Panel")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_channel_join_keyboard():
    keyboard = [
        [KeyboardButton("➕ Add Channel"),       KeyboardButton("✏️ Edit Channel")],
        [KeyboardButton("🗑️ Delete Channel"),    KeyboardButton("🕑 Check Interval")],
        [KeyboardButton("🔙 Back to Admin Tools")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_users_keyboard():
    keyboard = [
        [KeyboardButton("👤 User Count"), KeyboardButton("📈 User Stats")],
        [KeyboardButton("🔍 User Info"),  KeyboardButton("🔙 Back to Admin Panel")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_settings_keyboard():
    keyboard = [
        [KeyboardButton("🎯 OTP Bonus"),          KeyboardButton("🎁 Referral")],
        [KeyboardButton("🔢 Number Limit"),        KeyboardButton("🌍 Country OTP Bonus")],
        [KeyboardButton("🗑️ Reset All Users"),     KeyboardButton("🛠 Admin Tools")],
        [KeyboardButton("🔙 Back to Admin Panel")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_otp_bonus_keyboard():
    keyboard = [
        [KeyboardButton("🔛 OTP Bonus Toggle"),  KeyboardButton("💰 Set Bonus Amount")],
        [KeyboardButton("👤 Edit Balance"),       KeyboardButton("🔙 Back to Settings")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_referral_keyboard():
    keyboard = [
        [KeyboardButton("🔛 Referral Toggle"),   KeyboardButton("💰 Set Referral Bonus")],
        [KeyboardButton("📤 Set Min Withdraw"),   KeyboardButton("👤 Edit Balance")],
        [KeyboardButton("💸 Pending Withdraws"),  KeyboardButton("🔙 Back to Settings")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_manage_numbers_keyboard():
    keyboard = [
        [KeyboardButton("📲 𝑨𝒅𝒅 𝑵𝒖𝒎𝒃𝒆𝒓"),    KeyboardButton("🌐Add 𝑪𝒐𝒖𝒏𝒕𝒓𝒚")],
        [KeyboardButton("🔄 𝑹𝒆𝒔𝒆𝒕 𝑵𝒖𝒎𝒃𝒆𝒓"), KeyboardButton("🛠️ 𝑺𝒆𝒓𝒗𝒊𝒄𝒆𝒔")],
        [KeyboardButton("🔙 Back to Admin Panel")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_manage_admins_keyboard():
    keyboard = [
        [KeyboardButton("👥 Add Admin"),  KeyboardButton("🔧 Remove Admin")],
        [KeyboardButton("🔙 Back to Admin Panel")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_user_keyboard():
    keyboard = [
        [KeyboardButton("☎️ Get Number"),       KeyboardButton("🌍 Available Country")],
        [KeyboardButton("💰 My Balance"),        KeyboardButton("💸 Withdraw")],
        [KeyboardButton("🏆 Top Users"),         KeyboardButton("👥 Support Group")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def country_number_keyboard(country_id, otp_link, numbers=None):
    rows = []
    if numbers:
        for num in numbers:
            display = f"+{num}" if not str(num).startswith("+") else str(num)
            rows.append([
                InlineKeyboardButton(
                    display,
                    copy_text=CopyTextButton(text=display),
                )
            ])
    rows.append([InlineKeyboardButton("🔄 Change Number",     callback_data=f"another_{country_id}")])
    rows.append([InlineKeyboardButton("📲 GET OTP",           url=otp_link)])
    rows.append([InlineKeyboardButton("🔙 Back to Countries", callback_data="get_numbers")])
    return InlineKeyboardMarkup(rows)


def countries_inline_keyboard(countries_data):
    """
    countries_data: list of (country_id, country_name, available_count)
    Returns InlineKeyboardMarkup or None if no available numbers.
    """
    keyboard = []
    for country_id, country_name, available in countries_data:
        if available > 0:
            keyboard.append([
                InlineKeyboardButton(
                    f"{country_name} (+{available})",
                    callback_data=f"country_{country_id}",
                )
            ])
    return InlineKeyboardMarkup(keyboard) if keyboard else None
