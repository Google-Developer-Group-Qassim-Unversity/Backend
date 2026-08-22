# Google Forms Sync: Two Identifier Systems

## The Problem

Google Form submissions weren't syncing to `Submissions` rows - responses came in via the webhook, the background job ran, but every response was logged as "No email answer found" and submissions stayed stuck at `submission_type = "partial"` forever.

The sync code identified "the email question" in a Google Form response by a single hardcoded ID:

```python
EMAIL_QUESTION_ID = "310677703"
```

This assumed Google Forms question IDs are stable across forms ("every event's Google Form is a clone of one master template, so field IDs stay stable across forms"). That assumption is false.

## Background: two unrelated ID systems

Google Forms exposes each question under **two different identifiers**, from two unrelated product surfaces, and there is no documented mapping between them:

| System | Format | Where it's used | Stability across form clones |
|---|---|---|---|
| **Entry ID** | long decimal, e.g. `310677703` | Pre-filled link URLs (`entry.XXXXXXXXX=value`), rendered from the form's HTML `<input name>` | Stable - confirmed empirically, this is why the frontend's prefill works correctly |
| **`questionId`** | short hex, e.g. `128490c7` | Forms REST API (`forms.get()`, `forms.responses().list()`), assigned when the API layer manages the question | **Not stable** - a fresh ID per form, even for forms cloned from the same template |

Confirmed by checking two real event forms' schemas via `forms.get()`:

- Form 144 (chess tournament): the "Email" question's `questionId` was `196b7896`.
- Form 156 (test event): `196b7896` existed too, but on a completely different (multiple-choice) question. The actual email question there was `128490c7`.

So a hardcoded `questionId` is wrong on essentially every form - there's no single constant that works, and the two forms above prove the same ID can mean two different questions on different forms.

The frontend's prefill (`score-leaderboard-app/app/(google-form)/events/[id]/form/page.tsx`) uses **entry IDs**, which are the stable system - that's why prefilling the form when a member opens it has always worked fine, even while the backend's response-matching (which only ever sees `questionId`s, since that's the API's response format) was silently broken.

## The Fix

Instead of trusting either ID system to identify "the email question" ahead of time, scan every text answer in a response for the one value that's actually *shaped* like an email address:

```python
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def extract_email_answer(answers: dict) -> str | None:
    for answer in answers.values():
        answers_list = answer.get("textAnswers", {}).get("answers", [])
        if not answers_list:
            continue
        value = (answers_list[0].get("value") or "").strip()
        if value and EMAIL_PATTERN.match(value):
            return value.lower()
    return None
```

This works regardless of which `questionId` holds the email on a given form, and needs no per-form configuration or schema lookups.

## Implementation Locations

### 1. `app/routers/submissions.py`
- **What**: `extract_email_answer()`, replacing the old `extract_text_answer(answers, EMAIL_QUESTION_ID)`.
- **Used by**: `sync_form_submissions()` - the scheduled job that runs after every `POST /submissions/google/webhook` notification, matching Google responses to existing `partial` submissions by email.

### 2. `app/routers/submissions_manual.py`
- **What**: `sync_manual_form_submissions()` - the admin-triggered manual backfill path (creates new submissions directly, doesn't require a pre-existing `partial` row).
- **Why it needed the same fix**: it imported and called the same broken `extract_text_answer(answers, EMAIL_QUESTION_ID)` independently.

### 3. `score-leaderboard-app/app/(google-form)/events/[id]/form/page.tsx`
- **What**: `PERSONAL_EMAIL_ENTRY_ID` and friends (`UNI_ID_ENTRY_ID`, `NAME_ENTRY_ID`, `GENDER_ENTRY_ID`) - the entry-ID-based prefill.
- **Left as-is**: entry IDs are the stable system, so this still works. Commented to flag that these IDs are unrelated to the backend's `questionId`-based matching, so nobody "fixes" one by copying values from the other.

### 4. `tests/routers/test_submissions_sync.py`
- Unit tests for `extract_email_answer()`, including the real captured response payload from form 156 (the one that exposed this bug) as a regression fixture.

## Things to know going forward

- **Don't reintroduce a hardcoded `questionId`.** If a form ever needs another field matched from a response (not just email), it needs either shape-based detection like this, or a per-form schema lookup at sync time - a fixed constant will break on the next form.
- **Multiple email-shaped answers in one response** would currently resolve to whichever is found first in dict iteration order (Python 3.7+ preserves insertion order, which follows the API's own answer ordering). Not currently an issue on any known form template, but worth knowing if a future form ever legitimately asks for two email addresses.
- **Entry IDs are still assumed stable** for the frontend prefill. If that assumption ever turns out to be wrong for a new form template, prefill will silently no-op (Google Forms ignores unrecognized `entry.*` params) rather than error - the member just has to type the field manually. This doesn't affect sync correctness either way.
