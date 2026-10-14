"""Design's card on the team's Notion board: one page per request, kept in sync.

The design team works from a Notion board. When a request reaches Design the
backend writes its card there, and from then on the card follows the request:
returned, resubmitted, poster uploaded, dates moved by Logistics, Design done,
published, cancelled. Nothing is ever typed twice.

**What goes where.** The board's columns are what a designer sorts and filters
by: Status (the board's columns), the department, the design type, size and
file type, whether the content is final, the event's dates, the audience, where
it happens, who asked and how to reach them, the poster once it is up, and a
link back to the request. The page body is the brief itself: the idea, the
content to put in the design, instructions, image and reference links, the
event in full, who to contact, and what happened so far. The columns are
pushed to the database from ``PROPERTIES`` on first use, so the team never
builds the schema by hand; ``Designer`` is theirs and never written.

**Theirs stays theirs.** Everything the backend writes in the body lives in
synced blocks; a sync deletes those and writes fresh ones. Whatever the team
writes outside them - notes under the card, their own blocks, the Designer
column, a Status they moved to "In progress" - is left alone. Status is reset
only when the request reaches Design again (submit, resubmit), or when the
pipeline knows better (returned, done, published, cancelled).

**Never in the way.** Like the emails, the step commits first; the card is
written after, in a background task. The transition sets
``notion_sync_pending`` on Design's task in its own transaction, so a sync
that fails (Notion down, token revoked) leaves the flag set and the sweep
retries it every few minutes; the error is kept on the task for the request
page. With ``NOTION_TOKEN`` / ``NOTION_DESIGN_DATABASE_ID`` unset nothing is
sent and nothing retries. Staging writes to the same board, every title
prefixed ``(staging)``.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import config
from app.DB import event_pipeline as queries
from app.DB.main import db_session
from app.DB.schema import (
    EventRequests,
    EventRequestStage,
    EventRequestTasks,
    EventRequestTaskStatus,
    Members,
    PipelineHistory,
    PipelineHistoryAction,
    PipelineTeam,
)
from app.semesters import CLUB_TIMEZONE
from app.services import event_pipeline_clock as clock
from app.services import notion_client as notion
from app.services import pipeline_history as history
from app.services.notion_client import rich, text

logger = logging.getLogger(__name__)

# ``notion_sync_pending`` values. REOPENED also resets the card's Status.
PENDING_NONE = 0
PENDING_UPDATE = 1
PENDING_REOPENED = 2

# How many pending cards one sweep pushes.
SWEEP_BATCH = 20

# --------------------------------------------------------------------------- labels (Arabic · English)

STATUS_NEW = "New"
STATUS_IN_PROGRESS = "In progress"
STATUS_RETURNED = "Returned to team"
STATUS_DONE = "Done"
STATUS_PUBLISHED = "Published"
STATUS_CANCELLED = "Cancelled"
STATUSES = [
    (STATUS_NEW, "blue"),
    (STATUS_IN_PROGRESS, "yellow"),
    (STATUS_RETURNED, "red"),
    (STATUS_DONE, "green"),
    (STATUS_PUBLISHED, "purple"),
    (STATUS_CANCELLED, "gray"),
]

DESIGN_TYPES = {
    "poster": ("صورة إعلانية", "Promotional image", "🖼️"),
    "slides": ("سلايدات", "Slides", "📊"),
    "posts": ("منشورات", "Posts", "📱"),
    "reports": ("تقارير", "Reports", "📄"),
    "prints": ("مطبوعات", "Prints", "🖨️"),
    "other": ("أخرى", "Other", "🎨"),
}
SIZES = {
    "square": ("مربع", "Square"),
    "landscape": ("بالعرض", "Landscape"),
    "portrait": ("بالطول", "Portrait"),
    "other": ("أخرى", "Other"),
}
FILE_TYPES = {
    "png": ("PNG", "PNG"),
    "jpeg": ("JPEG", "JPEG"),
    "pdf": ("PDF", "PDF"),
    "powerpoint": ("PowerPoint", "PowerPoint"),
    "other": ("أخرى", "Other"),
}
CONTENT_STATUSES = {
    "final": ("المحتوى كامل ونهائي", "Final"),
    "needs_wording": ("المعلومات جاهزة وتحتاج صياغة", "Needs wording"),
}
EVENT_TYPES = {
    "course": ("دورة", "Course"),
    "bootcamp": ("معسكر", "Bootcamp"),
    "meetup": ("لقاء", "Meetup"),
    "workshop": ("ورشة", "Workshop"),
    "competition": ("مسابقة", "Competition"),
}
AUDIENCES = {
    "male": ("طلاب", "Male students"),
    "female": ("طالبات", "Female students"),
    "mixed": ("طلاب وطالبات", "Mixed"),
    "none": ("غير محددة", "Not specified"),
}
REGISTRATIONS = {
    "acceptance": ("بالقبول", "By acceptance"),
    "open": ("مفتوح", "Open"),
    "none": ("بدون تسجيل", "No registration"),
}
SCOPES = {"inside": ("داخل الجامعة", "Inside the university"), "outside": ("خارج الجامعة", "Outside the university")}
MODES = {"on_site": ("حضوري", "On-site"), "online": ("عن بعد", "Online")}
WHERE = {frozenset({"on_site"}): "On-site", frozenset({"online"}): "Online", frozenset({"on_site", "online"}): "Hybrid"}
TEAMS = {"design": "التصميم", "logistics": "اللوجستيات", "media": "الإعلام"}

BOARD_TITLE = "Design requests · طلبات التصميم"
BOARD_DESCRIPTION = (
    "كل بطاقة هنا طلب تصميم من لوحة الإدارة، تُكتب وتتحدّث تلقائيًا. "
    "Every card is a design request from the admin panel, written and kept up to date automatically. "
    "Yours to fill: Designer, and Status while you work on it."
)

# The board's columns, pushed with one PATCH. Order here is not the order on the board.
PROPERTIES: dict[str, dict] = {
    "Status": {"select": {"options": [{"name": name, "color": color} for name, color in STATUSES]}},
    "Department": {"select": {}},
    "Design type": {
        "select": {"options": [{"name": en, "color": "default"} for _ar, en, _icon in DESIGN_TYPES.values()]}
    },
    "Size": {"select": {"options": [{"name": en, "color": "default"} for _ar, en in SIZES.values()]}},
    "File type": {"select": {"options": [{"name": en, "color": "default"} for _ar, en in FILE_TYPES.values()]}},
    "Content": {
        "select": {
            "options": [
                {"name": CONTENT_STATUSES["final"][1], "color": "green"},
                {"name": CONTENT_STATUSES["needs_wording"][1], "color": "orange"},
            ]
        }
    },
    "Event date": {"date": {}},
    "Audience": {"select": {"options": [{"name": en, "color": "default"} for _ar, en in AUDIENCES.values()]}},
    "Where": {"select": {"options": [{"name": name, "color": "default"} for name in ("On-site", "Online", "Hybrid")]}},
    "Requested by": {"rich_text": {}},
    "Email": {"email": {}},
    "Phone": {"phone_number": {}},
    "Designer": {"people": {}},
    "Poster": {"files": {}},
    "Request": {"url": {}},
    "Submitted": {"date": {}},
}

# The database's title column, whatever the team called it. Learnt from the database on first use.
_title_property: dict[str, str] = {}


# --------------------------------------------------------------------------- marking, from inside transitions


def design_task(request: EventRequests) -> EventRequestTasks | None:
    return next((t for t in request.tasks if t.team == PipelineTeam.DESIGN), None)


def mark(session: Session, request: EventRequests, reopened: bool = False) -> None:
    """The card has to change: set the flag in the transition's own transaction.

    ``reopened`` is for the request reaching Design (again): the card's Status
    goes back to "New" even if a designer had moved it. Nothing happens while
    Design has not received the request, and nothing is sent from here.
    """
    task = design_task(request)
    if task is None or task.status == EventRequestTaskStatus.BRIEF:
        return
    task.notion_sync_pending = max(task.notion_sync_pending, PENDING_REOPENED if reopened else PENDING_UPDATE)
    session.flush()


def sync_after_commit(background_tasks: BackgroundTasks, request: EventRequests) -> None:
    """Queue the sync of a request whose transition has committed. Call after ``session.commit()``."""
    if not config.notion_enabled:
        return
    task = design_task(request)
    if task is not None and task.notion_sync_pending:
        background_tasks.add_task(sync_request, request.id)


def pending_request_ids(session: Session, limit: int = SWEEP_BATCH) -> list[str]:
    """Requests whose card still has to be written, oldest first, for the sweep to retry."""
    if not config.notion_enabled:
        return []
    rows = session.scalars(
        select(EventRequestTasks.request_id)
        .where(EventRequestTasks.team == PipelineTeam.DESIGN, EventRequestTasks.notion_sync_pending > PENDING_NONE)
        .order_by(EventRequestTasks.id)
        .limit(limit)
    ).all()
    return list(rows)


# --------------------------------------------------------------------------- the sync itself


def sync_request(request_id: str) -> bool:
    """Write the request's card, creating it the first time. True when the card is now up to date.

    Runs in its own session, after the transition committed. Design's task row
    is locked for the duration so a background task and the sweep never write
    the same card twice. A failure keeps the flag set and stores the error.
    """
    if not config.notion_enabled:
        return False
    with db_session() as session:
        request = queries.get_request(session, request_id)
        task = session.scalar(
            select(EventRequestTasks)
            .where(EventRequestTasks.request_id == request_id, EventRequestTasks.team == PipelineTeam.DESIGN)
            .with_for_update()
        )
        if request is None or task is None or task.status == EventRequestTaskStatus.BRIEF:
            return False
        if not task.notion_sync_pending:
            return True
        try:
            _push(session, request, task)
        except Exception as error:
            task.notion_error = str(error)[:2000]
            session.commit()
            logger.exception("Request %s: Notion card not written, kept for the sweep", request_id)
            return False
        task.notion_sync_pending = PENDING_NONE
        task.notion_synced_at = clock.now()
        task.notion_error = None
        session.commit()
        return True


def _push(session: Session, request: EventRequests, task: EventRequestTasks) -> None:
    database_id = config.NOTION_DESIGN_DATABASE_ID or ""
    ensure_schema(database_id)
    reset_status = task.notion_page_id is None or task.notion_sync_pending == PENDING_REOPENED
    properties = card_properties(request, task, reset_status=reset_status)
    rows = history.for_request(session, request.id)
    children = [synced(chunk) for chunk in _chunks(card_body(request, task, rows), 90)]
    icon = card_icon(task)

    if task.notion_page_id is None:
        if task.status == EventRequestTaskStatus.DONE or request.stage in (
            EventRequestStage.PUBLISHED,
            EventRequestStage.CANCELLED,
        ):
            # Design is past this request; a card now would only be noise.
            logger.info("Request %s: Design is done with it, no Notion card created", request.id)
            return
        page = notion.create_page(database_id, properties, children, icon=icon)
        task.notion_page_id = page["id"]
        task.notion_page_url = page.get("url")
        logger.info("Request %s: Notion card created %s", request.id, task.notion_page_url)
        return

    notion.update_page(task.notion_page_id, properties, icon=icon)
    for block in notion.list_children(task.notion_page_id):
        if block.get("type") == "synced_block" and not block["synced_block"].get("synced_from"):
            notion.delete_block(block["id"])
    notion.append_children(task.notion_page_id, children)
    logger.info("Request %s: Notion card updated", request.id)


def ensure_schema(database_id: str) -> str:
    """Push the board's columns once per process, and learn the name of its title column."""
    if database_id in _title_property:
        return _title_property[database_id]
    database = notion.call("GET", f"/databases/{database_id}")
    title_name = next(
        (name for name, prop in database.get("properties", {}).items() if prop.get("type") == "title"), "Name"
    )
    notion.update_database(database_id, BOARD_TITLE, BOARD_DESCRIPTION, PROPERTIES)
    _title_property[database_id] = title_name
    logger.info("Notion board %s: columns in place (title column %r)", database_id, title_name)
    return title_name


# --------------------------------------------------------------------------- the card's columns


def card_status(request: EventRequests, task: EventRequestTasks) -> str:
    if request.stage == EventRequestStage.CANCELLED:
        return STATUS_CANCELLED
    if request.stage == EventRequestStage.PUBLISHED:
        return STATUS_PUBLISHED
    if task.status == EventRequestTaskStatus.DONE:
        return STATUS_DONE
    if task.status == EventRequestTaskStatus.RETURNED:
        return STATUS_RETURNED
    return STATUS_NEW


def card_title(request: EventRequests) -> str:
    prefix = "" if config.ENV == "production" else f"({config.ENV}) "
    return f"{prefix}{request.title or 'بدون عنوان · Untitled'}"


def card_icon(task: EventRequestTasks) -> str:
    brief = task.brief or {}
    return DESIGN_TYPES.get(brief.get("design_type") or "other", DESIGN_TYPES["other"])[2]


def card_properties(request: EventRequests, task: EventRequestTasks, *, reset_status: bool) -> dict[str, Any]:
    brief = task.brief or {}
    title_name = _title_property.get(config.NOTION_DESIGN_DATABASE_ID or "", "Name")
    requester: Members = request.requester
    poster = (task.deliverable or {}).get("poster_url")
    modes = frozenset((request.day_modes or {}).values())
    status = card_status(request, task)

    properties: dict[str, Any] = {
        title_name: {"title": text(card_title(request))},
        "Department": _select(_option_name(request.department.ar_name or request.department.name)),
        "Design type": _select(_label(DESIGN_TYPES, brief.get("design_type"), 1)),
        "Size": _select(_label(SIZES, brief.get("size"), 1)),
        "File type": _select(_label(FILE_TYPES, brief.get("file_type"), 1)),
        "Content": _select(_label(CONTENT_STATUSES, brief.get("content_status"), 1)),
        "Event date": _date(request.start_date, request.end_date),
        "Audience": _select(_label(AUDIENCES, _value(request.audience), 1)),
        "Where": _select(WHERE.get(modes)),
        "Requested by": {"rich_text": text(requester.name)},
        "Email": {"email": requester.email or None},
        "Phone": {"phone_number": requester.phone_number or None},
        "Poster": {"files": [{"name": "poster", "type": "external", "external": {"url": poster}}] if poster else []},
        "Request": {"url": request_link(request)},
        "Submitted": _datetime(request.submitted_at),
    }
    # "New" is only forced when the request reaches Design; otherwise a designer's "In progress" stands.
    if reset_status or status != STATUS_NEW:
        properties["Status"] = _select(status)
    return properties


def request_link(request: EventRequests) -> str:
    return f"{config.ADMIN_APP_URL}/pipeline/requests/{request.id}"


def _value(enum_or_none) -> str | None:
    return enum_or_none.value if enum_or_none is not None else None


def _label(table: dict, key: str | None, index: int) -> str | None:
    return table[key][index] if key in table else None


def _both(table: dict, key: str | None) -> str | None:
    """ "Arabic · English" for a known key, the raw key otherwise."""
    if key is None:
        return None
    if key in table:
        ar, en = table[key][0], table[key][1]
        return ar if ar == en else f"{ar} · {en}"
    return str(key)


def _option_name(name: str) -> str:
    # Select option names cannot contain commas.
    return name.replace(",", "،").strip()[:100]


def _select(name: str | None) -> dict:
    return {"select": {"name": name} if name else None}


def _date(start, end) -> dict:
    if start is None:
        return {"date": None}
    return {"date": {"start": start.isoformat(), "end": end.isoformat() if end and end != start else None}}


def _datetime(value: datetime | None) -> dict:
    if value is None:
        return {"date": None}
    return {"date": {"start": value.replace(tzinfo=UTC).astimezone(CLUB_TIMEZONE).isoformat()}}


# --------------------------------------------------------------------------- the card's body


def card_body(request: EventRequests, task: EventRequestTasks, rows: list[PipelineHistory]) -> list[dict]:
    brief = task.brief or {}
    blocks: list[dict] = []

    blocks.append(
        callout(
            "🤖",
            "تُكتب هذه البطاقة تلقائيًا من لوحة الإدارة وتتحدّث مع كل تغيير في الطلب. "
            "ما تكتبونه أنتم خارج هذا الجزء يبقى كما هو. · "
            "This card is written by the admin panel and updated with every change to the request. "
            "Anything you write outside this part stays as it is.",
            "gray_background",
        )
    )
    if config.ENV != "production":
        blocks.append(
            callout(
                "🧪",
                f"نسخة تجريبية ({config.ENV})، احذفوها متى شئتم · A {config.ENV} copy; delete it freely.",
                "yellow_background",
            )
        )
    blocks.append(
        paragraph(
            rich(
                text(
                    "فتح الطلب في لوحة الإدارة · Open the request in the admin panel",
                    bold=True,
                    link=request_link(request),
                )
            )
        )
    )

    if request.stage == EventRequestStage.CANCELLED:
        blocks.append(
            callout(
                "🚫",
                "أُلغي هذا الطلب، لا حاجة لتصميمه · This request was cancelled; nothing to design.",
                "gray_background",
            )
        )
    elif request.stage == EventRequestStage.PUBLISHED:
        blocks.append(
            callout(
                "🎉",
                f"نُشرت الفعالية (رقم {request.event_id}) · The event is published (#{request.event_id}).",
                "green_background",
            )
        )
    elif task.status == EventRequestTaskStatus.RETURNED:
        due = _when(request.return_due_at)
        blocks.append(
            callout(
                "↩️",
                f"أُعيد الطلب للقسم الطالب للتعديل، ومهلته حتى {due}. لا شيء للتصميم حتى يُعاد إرساله. · "
                f"Returned to the requesting team to fix, due {due}. Nothing to design until it is resubmitted.",
                "red_background",
            )
        )
    elif task.status == EventRequestTaskStatus.DONE:
        blocks.append(
            callout(
                "✅",
                f"انتهى التصميم {_when(task.done_at)} · Design finished {_when(task.done_at)}.",
                "green_background",
            )
        )
    if request.return_notes:
        blocks.append(heading("↩️ ملاحظات الإعادة · Return notes"))
        blocks.extend(paragraphs(request.return_notes))
        if request.stage != EventRequestStage.RETURNED:
            blocks.append(
                paragraph(
                    text(
                        "أُعيد إرسال الطلب بعد هذه الملاحظات؛ راجعوا ما تغيّر. · The request was resubmitted after these notes; check what changed."
                    )
                )
            )

    blocks.append(heading("🎯 المطلوب · What to design"))
    blocks.append(fact("النوع · Type", _other(DESIGN_TYPES, brief.get("design_type"), brief.get("design_type_other"))))
    blocks.append(fact("المقاس · Size", _other(SIZES, brief.get("size"), brief.get("size_other"))))
    blocks.append(
        fact("صيغة الملف · File type", _other(FILE_TYPES, brief.get("file_type"), brief.get("file_type_other")))
    )

    blocks.append(heading("💡 الفكرة · The idea"))
    blocks.extend(paragraphs(brief.get("idea")))

    blocks.append(heading("📝 المحتوى · The content"))
    status = brief.get("content_status")
    if status == "final":
        blocks.append(
            callout(
                "✅", "المحتوى كامل ونهائي، يُستخدم كما هو · The content is final; use it as it is.", "green_background"
            )
        )
    elif status == "needs_wording":
        blocks.append(
            callout(
                "✍️", "المعلومات جاهزة وتحتاج صياغة · The information is there; it needs wording.", "orange_background"
            )
        )
    blocks.extend(paragraphs(brief.get("content")))

    if brief.get("instructions"):
        blocks.append(heading("ℹ️ تعليمات خاصة · Special instructions"))
        blocks.extend(paragraphs(brief.get("instructions")))

    blocks.append(heading("🔗 الصور والشعارات · Images & logos"))
    blocks.extend(links(brief.get("image_links")))
    blocks.append(heading("🎨 تصاميم مرجعية · Reference designs"))
    blocks.extend(links(brief.get("reference_links")))

    blocks.append(heading("📅 الفعالية · The event"))
    blocks.extend(event_facts(request))
    if request.description:
        blocks.append(fact("الوصف · Description", None))
        blocks.extend(paragraphs(request.description))
    if request.help_needed:
        blocks.append(fact("مساعدة مطلوبة · Help needed", request.help_needed))

    blocks.append(heading("👤 التواصل · Contact"))
    blocks.extend(contact_facts(request))

    blocks.append(heading("🕓 ما حدث · What happened"))
    lines = [line for row in reversed(rows) if (line := history_line(row))]
    blocks.extend(bullet(line) for line in lines[:15])
    if not lines:
        blocks.append(paragraph(text("—")))
    return blocks


def event_facts(request: EventRequests) -> list[dict]:
    facts: list[dict] = [fact("العنوان · Title", request.title)]
    facts.append(fact("النوع · Type", _both(EVENT_TYPES, _value(request.event_type))))
    if request.start_date and request.end_date:
        dates = request.start_date.isoformat()
        if request.end_date != request.start_date:
            dates += f" → {request.end_date.isoformat()}"
    else:
        dates = "لا توجد تواريخ حاليًا؛ ستُحدَّث · No dates right now; this updates when they are booked"
    facts.append(fact("التواريخ · Dates", dates))
    if request.daily_start_time and request.daily_end_time:
        facts.append(
            fact("الوقت اليومي · Daily time", f"{request.daily_start_time:%H:%M} – {request.daily_end_time:%H:%M}")
        )
    if request.day_modes:
        days = ", ".join(f"{day}: {_both(MODES, mode)}" for day, mode in sorted(request.day_modes.items()))
        facts.append(fact("كل يوم · Each day", days))
    facts.append(fact("المكان · Where", _both(SCOPES, _value(request.location_scope))))
    facts.append(fact("الفئة المستهدفة · Audience", _both(AUDIENCES, _value(request.audience))))
    registration = _both(REGISTRATIONS, _value(request.registration))
    if request.expected_accepted:
        registration = f"{registration} ({request.expected_accepted} متوقع قبولهم · expected accepted)"
    facts.append(fact("التسجيل · Registration", registration))
    if request.is_official is not None:
        facts.append(fact("رسمية · Official", "نعم · Yes" if request.is_official else "لا · No"))
    presenter = request.presenter_name
    if presenter and request.presenter_email:
        presenter = f"{presenter} ({request.presenter_email})"
    facts.append(fact("المقدّم · Presenter", presenter))
    if request.partners:
        facts.append(
            fact(
                "أقسام شريكة · Partner departments",
                "، ".join(p.department.ar_name or p.department.name for p in request.partners),
            )
        )
    return [f for f in facts if f is not None]


def contact_facts(request: EventRequests) -> list[dict]:
    requester: Members = request.requester
    facts = [
        fact("القسم الطالب · Requesting department", f"{request.department.ar_name} · {request.department.name}"),
        fact("صاحب الطلب · Requested by", requester.name),
        fact("البريد · Email", requester.email),
        fact("الجوال · Phone", requester.phone_number),
    ]
    submitter = request.submitter
    if submitter is not None and submitter.id != requester.id:
        facts.append(
            fact("أرسله · Submitted by", f"{submitter.name}" + (f" ({submitter.email})" if submitter.email else ""))
        )
    return facts


def history_line(row: PipelineHistory) -> str | None:
    """One line of "what happened", or None for steps a designer does not care about."""
    d = row.details or {}
    who = row.actor.name if row.actor else "تلقائيًا · automatic"
    action = row.action
    if action == PipelineHistoryAction.SUBMITTED:
        what = "أرسل الطلب إلى التصميم واللوجستيات · submitted the request to Design and Logistics"
    elif action == PipelineHistoryAction.RETURNED:
        what = "أعاد الطلب للقسم مع ملاحظات · returned the request to the team with notes"
    elif action == PipelineHistoryAction.RESUBMITTED:
        what = "أعاد إرسال الطلب إلى التصميم · resubmitted the request to Design"
        if d.get("late_days"):
            what += f" (متأخرًا {d['late_days']} يوم · {d['late_days']} day(s) late)"
    elif action == PipelineHistoryAction.DATES_MOVED:
        what = f"نقلت اللوجستيات التواريخ · Logistics moved the dates: {' → '.join(d.get('from') or ['?'])} ⟶ {' → '.join(d.get('to') or ['?'])}"
    elif action == PipelineHistoryAction.POSTER_UPLOADED:
        what = "استبدل البوستر · replaced the poster" if d.get("replaced") else "رفع البوستر · uploaded the poster"
    elif action == PipelineHistoryAction.TASK_DONE:
        team = d.get("team", "")
        what = f"أنهى دور {TEAMS.get(team, team)} · finished the {team} part"
    elif action == PipelineHistoryAction.PUBLISHED:
        what = f"نشر الفعالية (رقم {d.get('event_id')}) · published the event (#{d.get('event_id')})"
    elif action == PipelineHistoryAction.CANCELLED:
        what = "ألغى الطلب · cancelled the request"
    elif action == PipelineHistoryAction.DATES_BANNED:
        what = "أُغلق يوم من أيام الفعالية ففقد الطلب تواريخه · a day was closed, the request lost its dates"
    elif action == PipelineHistoryAction.REDATED:
        what = "اختار تواريخ جديدة · picked new dates"
    elif action == PipelineHistoryAction.DETAILS_EDITED and d.get("after_submit"):
        what = "عدّل بيانات الفعالية بعد الإرسال · edited the event details after submit"
    elif action == PipelineHistoryAction.BRIEF_EDITED and d.get("after_submit") and d.get("team") == "design":
        what = "عدّل طلب التصميم بعد الإرسال · edited the Design brief after submit"
    elif action == PipelineHistoryAction.EVENT_DELETED:
        what = "حُذفت الفعالية المنشورة · the published event was deleted"
    else:
        return None
    return f"{_when(row.at)} · {who} · {what}"


def _when(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.replace(tzinfo=UTC).astimezone(CLUB_TIMEZONE).strftime("%Y-%m-%d %H:%M")


def _other(table: dict, key: str | None, other: str | None) -> str | None:
    label = _both(table, key)
    if key == "other" and other:
        return f"{label}: {other}"
    return label


# --------------------------------------------------------------------------- blocks


def _block(kind: str, payload: dict) -> dict:
    return {"object": "block", "type": kind, kind: payload}


def heading(title: str) -> dict:
    return _block("heading_2", {"rich_text": text(title)})


def paragraph(rich_text: list[dict]) -> dict:
    return _block("paragraph", {"rich_text": rich_text})


def paragraphs(value: str | None, limit: int = 40) -> list[dict]:
    """Free text as one paragraph per line, a dash when there is nothing."""
    lines = [line.rstrip() for line in (value or "").splitlines() if line.strip()]
    if not lines:
        return [paragraph(text("—"))]
    if len(lines) > limit:
        lines = lines[: limit - 1] + ["\n".join(lines[limit - 1 :])]
    return [paragraph(text(line)) for line in lines]


def bullet(line: str) -> dict:
    return _block("bulleted_list_item", {"rich_text": text(line)})


def fact(label: str, value: str | None) -> dict:
    return _block("bulleted_list_item", {"rich_text": rich(text(f"{label}: ", bold=True), text(value or "—"))})


def links(values: list | None) -> list[dict]:
    items = [str(v).strip() for v in (values or []) if str(v).strip()]
    if not items:
        return [paragraph(text("لا شيء مرفق · nothing attached"))]
    return [
        _block("bulleted_list_item", {"rich_text": text(item, link=item if item.startswith("http") else None)})
        for item in items
    ]


def callout(emoji: str, body: str, color: str) -> dict:
    return _block("callout", {"icon": {"type": "emoji", "emoji": emoji}, "rich_text": text(body), "color": color})


def synced(children: list[dict]) -> dict:
    """An original synced block: the one kind of block the sync owns and may delete."""
    return _block("synced_block", {"synced_from": None, "children": children})


def _chunks(blocks: list[dict], size: int) -> list[list[dict]]:
    return [blocks[i : i + size] for i in range(0, len(blocks), size)]
