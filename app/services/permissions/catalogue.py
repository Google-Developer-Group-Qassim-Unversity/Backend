"""Every permission the admin app knows, in one place.

A permission is a key such as ``events.edit``. The keys live here, in code,
because routes check them; who *holds* each one is data (``shared_permissions``,
``department_permissions``, ``permission_grants``) that a super admin changes
without a deploy. A key in the database that is not in ``Perm`` is ignored.

Scope says what a permission applies to:

- ``club``: everywhere. Holding it through any department is enough.
- ``dept``: only things belonging to the department it is held for, such as
  that department's own events.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Scope = Literal["club", "dept"]


class Perm(StrEnum):
    # Basics
    ADMIN_ACCESS = "admin.access"
    EVENTS_VIEW = "events.view"
    MEMBERS_VIEW = "members.view"
    CLUB_STRUCTURE_VIEW = "club_structure.view"
    # Events and attendance
    EVENTS_CREATE = "events.create"
    EVENTS_EDIT = "events.edit"
    EVENTS_DELETE = "events.delete"
    ATTENDANCE_TAKE = "attendance.take"
    ATTENDANCE_BACKFILL = "attendance.backfill"
    ATTENDANCE_COPY = "attendance.copy"
    FORMS_MANAGE = "forms.manage"
    SUBMISSIONS_REVIEW = "submissions.review"
    # Emails and certificates
    EMAILS_EVENT = "emails.event"
    EMAILS_DIRECT = "emails.direct"
    EMAILS_BLAST = "emails.blast"
    EMAILS_LOGS = "emails.logs"
    CERTIFICATES_MANUAL = "certificates.manual"
    # Points
    POINTS_CATALOGUE = "points.catalogue"
    POINTS_CUSTOM = "points.custom"
    # People and structure
    MEMBERS_CREATE = "members.create"
    CLUB_STRUCTURE_MANAGE_ROSTER = "club_structure.manage_roster"
    CLUB_STRUCTURE_MANAGE = "club_structure.manage"
    SEMESTERS_MANAGE = "semesters.manage"
    PERMISSIONS_GRANT = "permissions.grant"
    PERMISSIONS_MANAGE = "permissions.manage"
    # Events pipeline
    PIPELINE_REQUEST = "pipeline.request"
    PIPELINE_BANS = "pipeline.bans"
    PIPELINE_DESIGN = "pipeline.design"
    PIPELINE_LOGISTICS = "pipeline.logistics"
    PIPELINE_MEDIA = "pipeline.media"
    PIPELINE_TEAMS = "pipeline.teams"
    # System
    UPLOADS = "uploads"
    CACHE_RESET = "cache.reset"
    FORMS_ADMIN = "forms.admin"
    SETTINGS_TEMPLATE_FORM = "settings.template_form"


@dataclass(frozen=True)
class PermInfo:
    scope: Scope
    label: str
    ar_label: str


CATALOGUE: dict[Perm, PermInfo] = {
    Perm.ADMIN_ACCESS: PermInfo("club", "Open the admin app", "فتح لوحة الإدارة"),
    Perm.EVENTS_VIEW: PermInfo("club", "See events", "عرض الفعاليات"),
    Perm.MEMBERS_VIEW: PermInfo("club", "See members", "عرض الأعضاء"),
    Perm.CLUB_STRUCTURE_VIEW: PermInfo("club", "See the club structure", "عرض الهيكل التنظيمي"),
    Perm.EVENTS_CREATE: PermInfo("dept", "Create events", "إنشاء الفعاليات"),
    Perm.EVENTS_EDIT: PermInfo("dept", "Edit events", "تعديل الفعاليات"),
    Perm.EVENTS_DELETE: PermInfo("dept", "Delete draft events", "حذف مسودات الفعاليات"),
    Perm.ATTENDANCE_TAKE: PermInfo("dept", "Take attendance", "تسجيل الحضور"),
    Perm.ATTENDANCE_BACKFILL: PermInfo("dept", "Import attendance", "استيراد الحضور"),
    Perm.ATTENDANCE_COPY: PermInfo("club", "Copy attendance between events", "نسخ الحضور بين الفعاليات"),
    Perm.FORMS_MANAGE: PermInfo("dept", "Manage registration forms", "إدارة نماذج التسجيل"),
    Perm.SUBMISSIONS_REVIEW: PermInfo("dept", "Review registrations", "مراجعة التسجيلات"),
    Perm.EMAILS_EVENT: PermInfo("dept", "Send an event's emails and certificates", "إرسال رسائل الفعالية وشهاداتها"),
    Perm.EMAILS_DIRECT: PermInfo("club", "Email specific people", "مراسلة أشخاص محددين"),
    Perm.EMAILS_BLAST: PermInfo("club", "Send the club-wide email", "إرسال البريد لجميع الأعضاء"),
    Perm.EMAILS_LOGS: PermInfo("club", "See email logs", "عرض سجل الرسائل"),
    Perm.CERTIFICATES_MANUAL: PermInfo("club", "Issue a certificate by hand", "إصدار شهادة يدويًا"),
    Perm.POINTS_CATALOGUE: PermInfo("club", "Manage point actions", "إدارة بنود النقاط"),
    Perm.POINTS_CUSTOM: PermInfo("club", "Award custom points", "منح نقاط مخصصة"),
    Perm.MEMBERS_CREATE: PermInfo("club", "Add members", "إضافة الأعضاء"),
    Perm.CLUB_STRUCTURE_MANAGE_ROSTER: PermInfo("dept", "Manage the department's roster", "إدارة أعضاء القسم"),
    Perm.CLUB_STRUCTURE_MANAGE: PermInfo(
        "club", "Manage departments and semesters' structures", "إدارة الأقسام والهياكل"
    ),
    Perm.SEMESTERS_MANAGE: PermInfo("club", "Manage semesters", "إدارة الفصول الدراسية"),
    Perm.PERMISSIONS_GRANT: PermInfo("dept", "Grant permissions to department members", "منح الصلاحيات لأعضاء القسم"),
    Perm.PERMISSIONS_MANAGE: PermInfo(
        "club", "Manage permissions and super admins", "إدارة الصلاحيات والمشرفين العامين"
    ),
    Perm.PIPELINE_REQUEST: PermInfo("dept", "Book and publish the department's events", "حجز فعاليات القسم ونشرها"),
    Perm.PIPELINE_BANS: PermInfo("club", "Close calendar days", "إغلاق أيام في التقويم"),
    Perm.PIPELINE_DESIGN: PermInfo("club", "Work Design's requests", "العمل على طلبات التصميم"),
    Perm.PIPELINE_LOGISTICS: PermInfo("club", "Work Logistics' requests", "العمل على طلبات اللوجستيات"),
    Perm.PIPELINE_MEDIA: PermInfo("club", "Work Media's requests", "العمل على طلبات الإعلام"),
    Perm.PIPELINE_TEAMS: PermInfo("club", "Choose the pipeline's teams", "تحديد فرق مسار الفعاليات"),
    Perm.UPLOADS: PermInfo("club", "Upload images and files", "رفع الصور والملفات"),
    Perm.CACHE_RESET: PermInfo("club", "Reset the leaderboard's cache", "تحديث ذاكرة لوحة الصدارة"),
    Perm.FORMS_ADMIN: PermInfo("club", "Maintain Google Forms syncing", "صيانة مزامنة نماذج Google"),
    Perm.SETTINGS_TEMPLATE_FORM: PermInfo("club", "See the form template", "عرض قالب النموذج"),
}

# Everyone on the current roster has these, with no assignment or grant.
STAFF_BASICS: frozenset[Perm] = frozenset({Perm.ADMIN_ACCESS, Perm.EVENTS_VIEW, Perm.CLUB_STRUCTURE_VIEW, Perm.UPLOADS})

# The roles that get shared and department permissions. Plain members get grants.
OFFICER_ROLES: frozenset[str] = frozenset({"leader", "vp"})


def parse(keys) -> frozenset[Perm]:
    """The known permissions among ``keys``; unknown ones (e.g. a removed key still in the database) are dropped."""
    known = {p.value for p in Perm}
    return frozenset(Perm(k) for k in keys if k in known)
