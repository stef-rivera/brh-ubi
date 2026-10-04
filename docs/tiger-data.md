# Tiger Data learning history

The base detector, saved corrections, cached speech and Park flow stay local. Tiger Cloud adds durable road-sign encounter history and structured practice analytics. Database work happens on one background worker, never on the video recognition or speech request path.

## Setup

1. Create a Tiger Cloud service with TimescaleDB enabled.
2. Add its PostgreSQL URL as `TIGER_DATABASE_URL` in the repo-root `.env`, preserving the supplied `sslmode=require`. The URL is a secret and must never be committed.
3. Restart the app. The background worker runs the additive `sql/tiger_events.sql` migration and imports existing detection/answer JSON logs plus review history. It does not delete or alter those originals. Historical rows with synthetic test drive IDs are excluded from backfill; real drives use `YYYY-MM-DDTHH-MM-SS` IDs.
4. Open **Progress**. The UI should report **connected** and pending events should drain. With no URL it explicitly reports local/not configured. A connection failure reports retrying and retains events.

Manual verification/backfill, run from the project root:

```sh
.venv-local/bin/python scripts/tiger_backfill.py
.venv-local/bin/python scripts/tiger_backfill.py --sync
```

The second command requires real credentials. No successful cloud connection is claimed until it succeeds.

## Data and API contract

`backend.tiger_data.integration.start()` starts a daemon worker; `stop()` wakes it for shutdown. `enqueue_event(kind, payload)` persists a compact JSON event in `.local/tiger/outbox.sqlite3`, then wakes the worker. Local durable writes occur inline; network/migration/query work never does. Existing primary logs also allow startup recovery if an outbox write fails.

Events are detection revisions, graded answers, correction history and optional drive/park session events. Stable SHA-256 keys over kind/version/canonical payload make repeated backfills idempotent. A revision timestamp determines the latest detection, so ignored candidates do not count as captured practice signs. Sync acknowledges local records only after remote commit; remote primary keys also suppress duplicates after an interrupted acknowledgment. Pending events survive app restarts.

`GET /api/progress` returns:

- `backend`: `tiger` when cloud query data is current, otherwise `local`.
- `status`: `connected`, `retrying`, or `not_configured`.
- `sessions`, `answers`, `accuracy`: accuracy is a **fraction between 0 and 1**, or null with no grades. Partial answers count as missed.
- `recent_sessions`: drive ID, last seen, signs, answers and correct count.
- `struggling_signs`: sign ID, attempts, missed, and per-sign accuracy as a percentage.
- `events_synced`, `pending`, `configured`, optional safe error text.
- When cloud data is current, `daily_answers` comes from Tiger's `time_bucket` SQL aggregation.

`GET /api/integrations/tiger/status` returns only integration state/counts.

Only explicit `correct`, `partial` or `incorrect` structured quiz grades count. Conversational voice practice currently has no structured assessment and is not counted. Corrections improve replay labels; they are not model training.

## Sponsor demo

Drive a clip, park, use the typed quiz and answer one question incorrectly. Open Progress to show the missed sign and drive history. Repeat the sign correctly in a later session to see improvement. With Tiger configured, show `road_coach_events` as a real hypertable and the daily answer aggregation. All network failures leave the live demo working from local storage.

No photo, video or audio blobs are uploaded. Detection metadata, text questions/answers and correction labels are stored in the configured database. Current analytics are per app deployment, not per authenticated driver; a production multi-user product needs driver identity/access rules. The outbox retains events for local fallback; very large deployments should add retention and bounded analytics queries.

Official references:

- [Tiger Cloud getting started](https://www.tigerdata.com/docs/get-started)
- [Hypertables](https://www.tigerdata.com/docs/learn/hypertables/understand-hypertables)
- [create_hypertable API](https://github.com/timescale/Tiger-Data-Docs/blob/main/src/content/docs/reference/timescaledb/hypertables/create_hypertable.mdx)
