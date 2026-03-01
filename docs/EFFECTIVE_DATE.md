# Effective Date for Attendance

## The Problem

Events that cross the midnight boundary create ambiguity in attendance tracking. When an event runs late into the night, attendance marked after midnight technically falls on the next calendar day, but semantically belongs to the "intended" event day.

### Example Scenarios

**Scenario 1: Single-day event crossing midnight**
- Event: Feb 1, 10:00 PM - Feb 2, 12:00 AM (2 hours, single session)
- Member marks attendance at 11:30 PM Feb 1 → stored as Feb 1
- Same member marks again at 12:15 AM Feb 2 → stored as Feb 2
- **Problem**: Calendar-based duplicate check sees Feb 1 ≠ Feb 2, allows duplicate attendance

**Scenario 2: Multi-day event crossing midnight**
- Event: Mar 2, 10:00 PM - Mar 4, 12:00 AM (intended days: Mar 2, Mar 3)
- Day 1: Member marks at 10:30 PM Mar 2 → stored as Mar 2
- Day 1: Member marks again at 12:30 AM Mar 3 → stored as Mar 3
- **Problem**: Both are "night of Mar 2" attendance, but calendar dates differ

**Scenario 3: Query filtering by day**
- Event: Mar 2, 10:00 PM - Mar 4, 12:00 AM
- Query: "Show me attendance for day 1 (Mar 2)"
- Attendance at 12:30 AM Mar 3 is stored as Mar 3
- **Problem**: Record is missed because calendar date is Mar 3, not Mar 2

## The Solution: Effective Date

We introduce the concept of an **effective date** - the "intended" day for attendance purposes. Early morning hours (before a configurable threshold) are considered part of the previous day.

### Configuration

- **Threshold**: `ATTENDANCE_EARLY_HOURS_THRESHOLD` in `app/config.py`
- **Default**: 6 (hours 00:00-05:59 count as previous day)
- **Location**: `app/config.py` → `Config.ATTENDANCE_EARLY_HOURS_THRESHOLD`

### How It Works

```python
def get_effective_date(dt: datetime, threshold: int) -> date:
    if dt.hour < threshold:
        return (dt - timedelta(days=1)).date()
    return dt.date()
```

| Actual Time | Threshold | Effective Date |
|-------------|-----------|----------------|
| Mar 3, 2:00 AM | 6 | Mar 2 |
| Mar 3, 5:59 AM | 6 | Mar 2 |
| Mar 3, 6:00 AM | 6 | Mar 3 |
| Mar 3, 10:00 AM | 6 | Mar 3 |

## Implementation Locations

### 1. `app/config.py`
- **What**: Defines `ATTENDANCE_EARLY_HOURS_THRESHOLD = 6`
- **Why**: Centralized configuration allows easy adjustment
- **How**: Property `Config.ATTENDANCE_EARLY_HOURS_THRESHOLD` exposes the value

### 2. `app/helpers.py`
- **What**: `get_effective_date(dt, threshold)` function
- **Why**: Reusable helper for Python-side effective date calculation
- **How**: Used in attendance duplicate checking logic

### 3. `app/routers/attendance.py` (Line ~122)
- **What**: Duplicate attendance check
- **Why**: Prevents marking attendance twice for the same "effective" day
- **How**: Compares `get_effective_date(member_log.date)` with `get_effective_date(datetime.now())`
```python
now_effective = get_effective_date(datetime.now(), config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
log_effective = get_effective_date(member_log.date, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
if log_effective == now_effective:
    raise HTTPException(...)  # Already marked for this effective day
```

### 4. `app/DB/logs.py` - `get_event_attendance()` (Line ~217)
- **What**: SQL query filtering by specific day number
- **Why**: Ensures day-based queries include records that crossed midnight
- **How**: Uses SQL CASE statement to compute effective date in the database
```python
effective_date = case(
    (func.HOUR(MembersLogs.date) < threshold,
     func.DATE(func.DATE_SUB(MembersLogs.date, text("INTERVAL 1 DAY")))),
    else_=func.DATE(MembersLogs.date)
)
stmt = stmt.where(effective_date == target_date)
```

## Edge Cases Handled

1. **Late-night attendance**: 2 AM attendance counts as the night before
2. **Early morning events**: Events starting at 6 AM or later use their actual date
3. **Multi-day events**: Each "effective day" is tracked independently
4. **Query consistency**: Both Python and SQL use the same threshold logic
