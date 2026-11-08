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


# ---------------------------------------------------------------- app 1.0.3 (app/services/push/cases.py)


def event_updated(
    name: str | None,
    starts: datetime,
    ends: datetime,
    location: str | None,
    *,
    day: int = 1,
    total_days: int = 1,
    online: bool = False,
    new_link: bool = False,
    back_on: bool = False,
) -> Text:
    """The event as it is now, not what changed: its next day, that day's date and hours, and its place.

    ``starts``/``ends`` are that day's, Riyadh time. ``back_on``: it was
    cancelled and is on again. ``new_link``: the join link changed.
    """
    title = event_name(name)
    parts: dict[str, list[str]] = {}
    for lang in ("ar", "en"):
        hours = f"{time_text(starts, lang)} {'إلى' if lang == 'ar' else '–'} {time_text(ends, lang)}"
        when = f"{date_text(starts, lang)}{SEP}{hours}"
        if total_days > 1:
            when = (f"اليوم {day} من {total_days}" if lang == "ar" else f"Day {day} of {total_days}") + SEP + when
        place = ("عن بُعد" if lang == "ar" else "Online") if online else isolate(place_label(location))
        link = ("رابط جديد للانضمام" if lang == "ar" else "New link to join") if new_link else ""
        parts[lang] = [when, place, link]
    return {
        "ar": {"title": f"{'عادت' if back_on else 'تحديث'}: {title}", "body": _join(*parts["ar"])},
        "en": {"title": f"{'Back on' if back_on else 'Updated'}: {title}", "body": _join(*parts["en"])},
    }


def event_cancelled(name: str | None, starts: datetime) -> Text:
    title = event_name(name)
    return {
        "ar": {"title": f"أُلغيت: {title}", "body": _join(date_time_text(starts, "ar"), "لن تُقام في موعدها")},
        "en": {"title": f"Cancelled: {title}", "body": _join(date_time_text(starts, "en"), "It will not take place")},
    }


def event_live(name: str | None, day: int, total_days: int) -> Text:
    title = event_name(name)
    if total_days > 1:
        ar_body, en_body = f"اليوم {day} من {total_days} · اضغط للانضمام", f"Day {day} of {total_days} · Tap to join"
    else:
        ar_body, en_body = "اضغط للانضمام", "Tap to join"
    return {
        "ar": {"title": f"بدأت الآن: {title}", "body": ar_body},
        "en": {"title": f"Live now: {title}", "body": en_body},
    }


def registration_closing(name: str | None, location: str | None, starts: datetime) -> Text:
    """Registration closes when the event starts at the latest; the text gives that time, not "in 24 hours"."""
    title = event_name(name)
    place = isolate(place_label(location))
    return {
        "ar": {"title": f"آخر فرصة للتسجيل: {title}", "body": _join(f"تبدأ {date_time_text(starts, 'ar')}", place)},
        "en": {
            "title": f"Last chance to register: {title}",
            "body": _join(f"Starts {date_time_text(starts, 'en')}", place),
        },
    }


def certificate(name: str | None) -> Text:
    title = event_name(name)
    return {
        "ar": {"title": "شهادتك جاهزة", "body": _join(title, "أُرسلت إلى بريدك")},
        "en": {"title": "Your certificate is ready", "body": _join(title, "Sent to your email")},
    }


def event_content(name: str | None, kind: str, label: str | None) -> Text:
    """``kind`` is ``slides`` or ``recording``; ``label`` is the name the organizers gave the link."""
    title = event_name(name)
    body = isolate(re.sub(r"\s+", " ", (label or "").strip())) or title
    if kind == "recording":
        ar_title, en_title = f"أُضيف التسجيل: {title}", f"Recording added: {title}"
    else:
        ar_title, en_title = f"أُضيفت الشرائح: {title}", f"Slides added: {title}"
    return {"ar": {"title": ar_title, "body": body}, "en": {"title": en_title, "body": body}}


def weekly_summary(week_points: int, semester_points: int) -> Text:
    total_ar = f"{LRI}{semester_points}{PDI} {ar_points(semester_points)} هذا الفصل"
    total_en = f"{semester_points} {'point' if semester_points == 1 else 'points'} this semester"
    if week_points > 0:
        week_ar = f"{LRI}+{week_points}{PDI} {ar_points(week_points)} هذا الأسبوع"
        week_en = f"+{week_points} {'point' if week_points == 1 else 'points'} this week"
        ar_body, en_body = _join(week_ar, total_ar), _join(week_en, total_en)
    else:
        ar_body, en_body = total_ar, total_en
    return {
        "ar": {"title": "ملخص أسبوعك", "body": ar_body},
        "en": {"title": "Your week at GDG Qassim", "body": en_body},
    }


def welcome() -> Text:
    return {
        "ar": {
            "title": f"أهلًا بك في {isolate('GDG Qassim')}",
            "body": "ستصلك هنا تذكيرات فعالياتك ونقاطك وأخبار النادي",
        },
        "en": {
            "title": "Welcome to GDG Qassim",
            "body": "Your event reminders, points and club news will show up here",
        },
    }


# ---------------------------------------------------------------- app 1.0.3 build 6 (app/services/push/rank.py)


def rank_top5(rank: int) -> Text:
    """Entered the top 5: ``rank`` is the member's rank now (2-5; #1 is ``rank_first``)."""
    return {
        "ar": {"title": "دخلت الخمسة الأوائل!", "body": f"صرت رقم {LRI}{rank}{PDI} في ترتيب الفصل. كمّل، الأول قريب!"},
        "en": {
            "title": "You're in the top 5!",
            "body": f"You're #{rank} this semester. Keep going, first place is close!",
        },
    }


def rank_first(semester_points: int) -> Text:
    """Reached #1 (alone or shared): ``semester_points`` is the member's total this semester."""
    # After "بـ" the dual is نقطتين; the other forms are the app's.
    unit = "نقطتين" if semester_points == 2 else ar_points(semester_points)
    return {
        "ar": {
            "title": "أنت الأول!",
            "body": f"تصدّرت ترتيب الأعضاء هذا الفصل بـ{LRI}{semester_points}{PDI} {unit}. خلّك فوق!",
        },
        "en": {
            "title": "You're #1!",
            "body": f"You lead the members this semester with {semester_points} "
            f"{'point' if semester_points == 1 else 'points'}. Stay on top!",
        },
    }


def rank_left() -> Text:
    return {
        "ar": {
            "title": "تقدر ترجع!",
            "body": "طلعت من الخمسة الأوائل، بس نبي نشوفك دايم الأول. الفعالية الجاية فرصتك!",
        },
        "en": {
            "title": "You can get back in!",
            "body": "You dropped out of the top 5, but we want to see you first, always. Your next event is your chance!",
        },
    }
