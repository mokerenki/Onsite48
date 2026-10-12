# Onsite48 Legacy Schema Recovery Hotfix

**Scope:** Startup failure when an existing PostgreSQL volume contains legacy Onsite48 objects but lacks a usable Alembic revision record.

## What the supplied logs prove

The backend repeatedly ran the first migration:

```text
Running upgrade -> 20260812_01, Create Onsite48 product foundation.