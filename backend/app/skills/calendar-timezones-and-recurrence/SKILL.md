---
name: calendar-timezones-and-recurrence
description: Create calendar events with correct timezones, DST awareness, and RRULE recurrence patterns.
---

# Calendar Timezones & Recurrence

## Timezones
- Always store and send times with explicit IANA zones (e.g. `America/New_York`).
- Convert with `zoneinfo` (Python 3.9+) or `pytz` if needed.
- When the user says "3pm tomorrow", ask or infer zone from user profile / prior context; never assume UTC silently.

## Recurrence (RRULE)
- Daily: `FREQ=DAILY;COUNT=5`
- Weekly on days: `FREQ=WEEKLY;BYDAY=MO,WE,FR`
- Monthly: `FREQ=MONTHLY;BYMONTHDAY=15`
- Until: `UNTIL=20261231T235959Z`

## Procedure
1. Normalize start/end to aware datetimes.
2. Build event payload with timezone + optional RRULE.
3. Use draft/create calendar tool; confirm before any external write if policy requires.
