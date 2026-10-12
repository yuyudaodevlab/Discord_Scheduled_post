import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class UserError(Exception):
    """利用者にそのまま表示できるエラー。"""


def utcnow():
    return datetime.now(UTC)


def timezone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise UserError("タイムゾーンが見つかりません。Asia/Tokyo などのIANA名を指定してください。") from None


def parse_time(value, zone="Asia/Tokyo", now=None):
    now = now or utcnow()
    value = value.strip()
    match = re.fullmatch(r"([0-9]+)\s*(秒後|分後|時間後|日後|s|m|h|d)", value)
    try:
        if match:
            units = {"秒後": 1, "s": 1, "分後": 60, "m": 60,
                     "時間後": 3600, "h": 3600, "日後": 86400, "d": 86400}
            result = now + timedelta(seconds=int(match[1]) * units[match[2]])
        else:
            if not re.fullmatch(r"[0-9]{4}([-\/])[0-9]{2}\1[0-9]{2} [0-9]{2}:[0-9]{2}(:[0-9]{2})?", value):
                raise ValueError
            naive = datetime.fromisoformat(value.replace("/", "-"))
            tz = timezone(zone)
            candidates = {naive.replace(tzinfo=tz, fold=f).astimezone(UTC)
                          for f in (0, 1)
                          if naive.replace(tzinfo=tz, fold=f).astimezone(UTC).astimezone(tz).replace(tzinfo=None) == naive}
            if not candidates:
                raise UserError("夏時間への切替で存在しない日時です。別の時刻を指定してください。")
            if len(candidates) > 1:
                raise UserError("夏時間の切替で重複する日時です。相対時間で指定してください。")
            result = candidates.pop()
    except (ValueError, OverflowError):
        raise UserError("日時を確認してください。例：2026-10-30 21:00、30秒後、10m") from None
    if result <= now:
        raise UserError("指定された時刻はすでに過ぎています。")
    return result.astimezone(UTC)


def split_message(content, limit=2000):
    """Raw contentを失わず分割。UTF-16単位でも上限を超えない。"""
    chunks = []
    while content:
        used = end = 0
        for char in content:
            size = 2 if ord(char) > 0xFFFF else 1
            if used + size > limit:
                break
            used += size
            end += 1
        if end < len(content):
            paragraph = content.rfind("\n\n", 0, end)
            newline = content.rfind("\n", 0, end)
            boundary = paragraph + 2 if paragraph >= 0 else newline + 1
            if boundary > 0:
                end = boundary
        chunks.append(content[:end])
        content = content[end:]
    return chunks


def local_time(value, zone):
    return datetime.fromisoformat(value).astimezone(timezone(zone)).strftime("%Y年%m月%d日 %H:%M:%S")
