"""The text of each push, in Arabic and English, written when the outbox row is.

Dates and times read the way the app shows them: the Gregorian calendar,
Western digits and Riyadh time ("13 أكتوبر · 10:30 ص", "Oct 13 · 10:30 AM").
Event names and places are user content, so each is wrapped in a directional
isolate (U+2068 ... U+2069), like the app's ``isolate()``: a Latin name keeps
its own direction inside an Arabic line and the punctuation stays put.
"""

import re
from datetime import datetime

FSI = "\u2068"
LRI = "\u2066"
PDI = "\u2069"
SEP = " · "

AR_MONTHS = (
    "يناير",
    "فبراير",
    "مارس",
    "أبريل",
    "مايو",
    "يونيو",
    "يوليو",
    "أغسطس",
    "سبتمبر",
    "أكتوبر",
    "نوفمبر",
    "ديسمبر",
)
EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# The app's placeLabel(): organisers type emoji into places ("Google Meet 📍").
_PICTOGRAPHS = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\u2b50\u2b55\ufe0f\u200d\u20e3]")
_NO_PLACE = re.compile(r"(none|unknown|unkown)", re.IGNORECASE)

Text = dict[str, dict[str, str]]


def isolate(text: str) -> str:
    return f"{FSI}{text}{PDI}" if text else ""


def event_name(name: str | None) -> str:
    return isolate(re.sub(r"\s+", " ", (name or "").strip()))


def place_label(location: str | None) -> str:
    text = re.sub(r"\s+", " ", _PICTOGRAPHS.sub("", location or "")).strip()
    return "" if _NO_PLACE.fullmatch(text) else text


def time_text(riyadh: datetime, lang: str) -> str:
    hour = riyadh.hour % 12 or 12
    pm = riyadh.hour >= 12
    period = ("م" if pm else "ص") if lang == "ar" else ("PM" if pm else "AM")
    return f"{hour}:{riyadh.minute:02d} {period}"


def date_text(riyadh: datetime, lang: str) -> str:
    if lang == "ar":
        return f"{riyadh.day} {AR_MONTHS[riyadh.month - 1]}"
    return f"{EN_MONTHS[riyadh.month - 1]} {riyadh.day}"


def date_time_text(riyadh: datetime, lang: str) -> str:
    return f"{date_text(riyadh, lang)}{SEP}{time_text(riyadh, lang)}"


def _join(*parts: str) -> str:
    return SEP.join(part for part in parts if part)


def ar_points(count: int) -> str:
    """The app's rule: 1 نقطة, 2 نقطتان, 3-10 نقاط, 11 and up نقطة (by the last two digits)."""
    if count == 2:
        return "نقطتان"
    return "نقاط" if 3 <= count % 100 <= 10 else "نقطة"


def reminder(name: str | None, location: str | None, starts: datetime, day: int, total_days: int) -> Text:
    """``starts`` is the day's start, Riyadh time. The time, not "in an hour": a late acceptance gets it too."""
    title = event_name(name)
    place = isolate(place_label(location))
    if total_days > 1:
        ar_body = f"اليوم {day} من {total_days} يبدأ الساعة {time_text(starts, 'ar')}"
        en_body = f"Day {day} of {total_days} starts at {time_text(starts, 'en')}"
    else:
        ar_body = f"تبدأ اليوم الساعة {time_text(starts, 'ar')}"
        en_body = f"Starts today at {time_text(starts, 'en')}"
    return {
        "ar": {"title": f"تذكير: {title}", "body": _join(ar_body, place)},
        "en": {"title": f"Reminder: {title}", "body": _join(en_body, place)},
    }


def accepted(name: str | None, starts: datetime) -> Text:
    title = event_name(name)
    return {
        "ar": {"title": "تم قبول تسجيلك", "body": _join(title, date_time_text(starts, "ar"))},
        "en": {"title": "Registration accepted", "body": _join(title, date_time_text(starts, "en"))},
    }


def points(name: str | None, count: int) -> Text:
    title = event_name(name)
    plus = f"{LRI}+{count}{PDI}"
    return {
        "ar": {"title": f"{plus} {ar_points(count)}", "body": _join(title, "أُضيفت إلى رصيدك")},
        "en": {
            "title": f"+{count} {'point' if count == 1 else 'points'}",
            "body": _join(title, "added to your balance"),
        },
    }


def new_event(name: str | None, location: str | None, starts: datetime) -> Text:
    title = event_name(name)
    place = isolate(place_label(location))
    return {
        "ar": {"title": f"فعالية جديدة: {title}", "body": _join(date_time_text(starts, "ar"), place)},
        "en": {"title": f"New event: {title}", "body": _join(date_time_text(starts, "en"), place)},
    }


def broadcast(title_ar: str, body_ar: str, title_en: str | None, body_en: str | None) -> Text:
    return {
        "ar": {"title": title_ar, "body": body_ar},
        "en": {"title": title_en or title_ar, "body": body_en or body_ar},
    }
