import re


def telegram_chat_url(username: str | None, telegram_user_id: str) -> str:
    """Use the same safe Telegram username rule everywhere in the admin UI."""
    normalized = (username or "").lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", normalized):
        return f"https://t.me/{normalized}"
    return f"tg://user?id={telegram_user_id}"
