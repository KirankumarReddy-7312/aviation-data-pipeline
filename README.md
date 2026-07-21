# Aviation Data Pipeline — Production Build

A serverless, event-driven pipeline that ingests flight and reference data
from the [AviationStack](https://aviationstack.com) API, lands it through a
Bronze/Silver/Serving medallion architecture, and exposes it two ways:

- **A DynamoDB-backed REST API** for chatbots, agents, and apps that need a
  single flight/airport/airline lookup in single-digit milliseconds.
- **Athena/Glue over the Silver CSV layer** for BI tools (QuickSight, or any
  Athena-speaking client) doing ad-hoc SQL and historical analysis.

This directory is a from-scratch, production-hardened rebuild of the
`Aviation_Pipeline_Final.docx` design doc it started from. See
[What changed from the original design doc](#what-changed-from-the-original-design-doc)
for the specific gaps it closes.

## Architecture

```
                    ┌──────────────────────┐
   EventBridge      │  flights: every 12h  │  (free-plan defaults; see
   (2 schedules) ───┤  reference: monthly  │   "AviationStack plan & request
                    └──────────┬───────────┘   budget" below)
                               ▼
                    ┌─────────────────────┐
                    │ Lambda 1            │   AviationStack API
                    │ read_end_points     │──▶ (8 endpoints, paginated,
                    │                     │    retried, Secrets Manager key)
                    └──────────┬──────────┘
                               ▼
                 S3  raw/<entity>/<ts>.json     (Bronze — immutable archive)
                               │  S3 event (raw/*.json)
                               ▼
                    ┌─────────────────────┐
                    │ Lambda 2            │
                    │ raw_prepared        │
                    └──────────┬──────────┘
                               ▼
                 S3  prepared/<entity>/<ts>.csv (Silver — clean, typed columns)
                               │  S3 event (prepared/*.csv)          │
                               ▼                                     ▼
                    ┌─────────────────────┐          ┌───────────────────────┐
                    │ Lambda 3            │          │ Glue Catalog / Athena  │
                    │ prepared_dynamodb   │          │ (BI / QuickSight)      │
                    └──────────┬──────────┘          └───────────────────────┘
                               ▼
                 DynamoDB AviationMasterTable (Serving — single table design)
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Lambda 4            │
                    │ query_api           │◀── API Gateway + API key
                    └─────────────────────┘        (chatbot / app clients)

   Every Lambda writes structured JSON logs + CloudWatch EMF metrics and has
   a DLQ (for the 3 async-invoked functions) wired to an SNS alert topic.
```

Every stage (extract, transform, load, query) shares one file,
[`lambdas/common/python/aviation_common/config.py`](lambdas/common/python/aviation_common/config.py),
as the single source of truth for entity columns, PK/SK key patterns, and
required fields. Adding an entity or renaming a column happens in one place.

## The API endpoints (AviationStack, `api.aviationstack.com/v1`)

| Entity | Endpoint | Refresh (free-plan default) | Records (approx.) | Schema |
|---|---|---|---|---|
| Flights | `/flights` | every 12 hours | 480K+ total, 100/page (free plan caps `limit`) | ✅ verified |
| Airports | `/airports` | monthly | ~6,700 | ✅ verified |
| Airlines | `/airlines` | monthly | ~13,000 | ✅ verified |
| Aircraft Types | `/aircraft_types` | monthly | ~300 | ✅ verified |
| Cities | `/cities` | monthly | ~9,400 | ✅ verified |
| Countries | `/countries` | monthly | ~250 | ✅ verified |
| Routes *(added)* | `/routes` | monthly, opt-in | — | ⚠️ unverified, see below |
| Airplanes *(added)* | `/airplanes` | monthly, opt-in | — | ⚠️ unverified, see below |

### AviationStack plan & request budget

This project targets AviationStack's **Free plan: 100 requests/month,
total, shared across every endpoint** (not 100/endpoint). One "request" =
one page of results from one endpoint call. The schedule defaults in
`terraform/variables.tf` are deliberately conservative to fit inside that:

| Schedule | Cadence | Pages/run | Requests/month |
|---|---|---|---|
| `flights_schedule` | every 12 hours | 1 | 2 × 30 = **60** |
| `reference_schedule` (5 entities) | monthly | 1 each | **5** |
| **Total** | | | **65 / 100**, ~35 headroom for manual testing |

Only `airports`, `airlines`, `aircraft_types`, `cities`, `countries` are on
the reference schedule for the same reason — every entity added to it costs
1 more request/month. `routes` and `airplanes` stay opt-in even after their
schema is verified; add them to `reference_schedule`'s `entities` list in
`terraform/eventbridge.tf` only if you have budget for it (+1 request/month
each) and confirm your plan actually returns data for them (check with
`scripts/check_all_endpoints.py`, below).

If you're on a **paid AviationStack plan**, none of this applies — raise
`flights_schedule_expression`, `reference_schedule_expression`,
`flights_max_pages`, and `reference_max_pages` in `terraform.tfvars` (see
the commented examples in `terraform.tfvars.example`) back toward real-time
(`rate(10 minutes)` / daily / higher page caps).

### Checking which endpoints your key can actually reach

Free-plan feature lists vary (e.g. whether `routes` is included depends on
the specific plan), so don't guess — ask your key directly:

```bash
AVIATIONSTACK_API_KEY=xxxx python scripts/check_all_endpoints.py
```

Prints `OK` / `DENIED` / `FAILED` for each of the 8 entities without ever
printing the key itself. Run this once after signing up, before your first
`terraform apply`, so `DEFAULT_FETCH_ENTITIES` and the EventBridge
`entities` lists only include what your plan actually supports.

**"Verified" vs "unverified" schema:** the original 6 endpoints' field names
came from the source design doc and match what Lambda 2/3 expect. `routes`
and `airplanes` were added in this build because they round out the data
model (a route is what a flight is scheduled *against*; an airplane record
gives fleet/tail-number detail a chatbot might get asked about) but their
`extract()` field mapping in `config.py` is a best-effort guess, not
something confirmed against a live API response. They're fully wired
end-to-end — Lambda 2/3 already know how to handle them — but Lambda 1
excludes them from its default fetch set (`DEFAULT_FETCH_ENTITIES`) until
you:

```bash
AVIATIONSTACK_API_KEY=xxxx python scripts/inspect_endpoint.py routes
```

...compare the printed field names against `_routes_extract()` /
`_airplanes_extract()` in `config.py`, fix the mapping if needed, flip
`schema_verified: True`, and add the entity to the reference EventBridge
schedule's `entities` list in `terraform/eventbridge.tf`. Because Bronze
storage archives the complete raw API response regardless of what Silver
extracts, getting the mapping wrong the first time costs a Silver/DynamoDB
backfill, not lost data.

Every fetch uses `urllib.request` only (Lambda's Python runtime doesn't
bundle `requests`, and pulling in a Lambda Layer for a handful of GET calls
isn't worth it), with exponential-backoff retry on 429/5xx and pagination
bounded by both a page-count cap and the Lambda's own remaining time budget
— see `lambdas/common/python/aviation_common/http.py`.

## DynamoDB: single table design

One table, `AviationMasterTable`, `PAY_PER_REQUEST`, point-in-time recovery
on, one GSI (`GSI1`) used for the airline→flights access pattern.

| Entity | PK | SK | GSI1PK | GSI1SK |
|---|---|---|---|---|
| Flights | `FLIGHT#{iata}` | `DATE#{date}` | `AIRLINE#{iata}` | `DATE#{date}` |
| Airports | `AIRPORT#{iata}` | `METADATA` | — | — |
| Airlines | `AIRLINE#{iata}` | `METADATA` | — | — |
| Aircraft Types | `AIRCRAFT#{iata}` | `METADATA` | — | — |
| Cities | `CITY#{iata}` | `METADATA` | — | — |
| Countries | `COUNTRY#{iso2}` | `METADATA` | — | — |
| Routes | `ROUTE#{flight_iata}` | `METADATA` | — | — |
| Airplanes | `AIRPLANE#{registration}` | `METADATA` | — | — |

## Query API (chatbot / app consumption)

Deployed as an API Gateway REST API in front of Lambda 4, requiring an API
key (`terraform output query_api_key_id`, then
`aws apigateway get-api-key --api-key <id> --include-value` to read it).
Rate-limited (10 req/s, burst 20, 100K/day) as a backstop against a runaway
agent retry loop.

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | No API key required |
| GET | `/flights/{flight_iata}?date=YYYY-MM-DD` | Defaults to today (UTC) |
| GET | `/airlines/{iata}/flights?date=YYYY-MM-DD&limit=&next_token=` | GSI1 query, paginated |
| GET | `/airports/{iata}` | |
| GET | `/airlines/{iata}` | |
| GET | `/aircraft-types/{iata}` | |
| GET | `/cities/{iata}` | |
| GET | `/countries/{iso2}` | |
| GET | `/routes/{flight_iata}` | Only populated once `routes` is verified & enabled |
| GET | `/airplanes/{registration_number}` | Only populated once `airplanes` is verified & enabled |

```bash
curl -H "x-api-key: $API_KEY" \
  "https://<api-id>.execute-api.<region>.amazonaws.com/v1/flights/CA4127?date=2026-02-11"
```

Responses strip internal DynamoDB bookkeeping fields (`PK`, `SK`, `GSI1PK`,
`GSI1SK`, `source_key`) so a chatbot/LLM function-calling layer gets a clean
JSON object back. This API is meant to be called from a backend service
(chatbot server, agent tool, BI middleware) — CORS preflight isn't wired up,
since the API key shouldn't be exposed to browser JS anyway.

## BI (Athena / QuickSight)

Silver-layer CSVs are cataloged in Glue database `aviation_silver_db`, one
table per entity, pointing at `s3://<bucket>/prepared/<entity>/`. Query via
the `aviation-pipeline-bi` Athena workgroup:

```sql
SELECT airline_iata, flight_status, count(*)
FROM aviation_silver_db.flights
WHERE flight_date = '2026-02-11'
GROUP BY airline_iata, flight_status;
```

Point QuickSight (or any Athena-compatible BI tool) at that same workgroup.
This path is intentionally separate from the query API: BI does ad-hoc scans
over history, which is exactly what you don't want hitting DynamoDB.

## Deploying

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # fill in your AviationStack API key
terraform init
terraform plan
terraform apply
```

See [RUNBOOK.md](RUNBOOK.md) for day-2 operations: replaying a DLQ, rotating
the API key, verifying a new entity schema, responding to alarms.

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

36 tests cover: entity config consistency, HTTP retry/backoff/pagination
behavior, and each Lambda's handler logic (via `moto`-mocked S3/DynamoDB/
Secrets Manager — no AWS account needed to run them).

## What changed from the original design doc

The source doc (`Aviation_Pipeline_Final.docx`) was training/demo material:
useful for explaining the architecture, but its Lambda code was not meant to
run as-is. This build fixes:

- **Only 2 of 6 entities were implemented** in Lambda 2 and Lambda 3 (flights
  + airports); the other 4 were `# ... follow the same pattern` comments.
  All entities now run off the same `ENTITY_CONFIG`, so Lambda 2/3 can't
  silently drift out of sync with each other, and adding an entity is a
  config change, not a new code path.
- **No pagination** — the extractor called each endpoint once with
  `limit: 100`, which on an endpoint documented as having 480K+ flight
  records would silently return the first page forever. Lambda 1 now
  paginates with a configurable page cap and a Lambda-timeout-aware cutoff.
- **Plaintext API key in an env var** — moved to Secrets Manager, cached
  per warm start.
- **No retry/backoff** — a single 429 or transient 5xx killed the whole
  entity fetch for that run. Added exponential backoff with jitter.
- **No error isolation guarantees beyond a bare `try/except` with `print`**
  — replaced with structured JSON logs, CloudWatch EMF metrics per entity,
  and DLQs so a failure is both alarmable and replayable.
- **Fetching every entity every 10 minutes** — reference data (airports,
  airlines, etc.) barely changes; only `flights` needs a 10-minute cadence.
  Split into two EventBridge schedules to cut API calls and Lambda time.
- **No IaC at all** — this build adds the full Terraform stack (S3, DynamoDB,
  IAM, Lambda, EventBridge, S3 notifications, SQS DLQs, CloudWatch
  alarms/dashboard, Secrets Manager, API Gateway, Glue/Athena).
- **No consumption layer** — the doc mentioned Athena and DynamoDB access
  patterns conceptually but built neither a Glue Catalog nor any API in
  front of DynamoDB. Both are added here.
- **Syntax bugs in the "code"** (`deflambda_handler`, `Addsentity_type`,
  missing spaces/indentation throughout) — the doc's code blocks were never
  meant to be copy-pasted and run.

## Known limitations / next steps

- `routes` and `airplanes` need their schema verified against a live API key
  before they'll actually populate (see above).
- No gold-layer aggregation job (e.g., daily on-time-performance rollups) —
  Athena can compute these ad hoc today; a scheduled CTAS or Glue job would
  make repeat BI queries cheaper if that becomes a real workload.
- No CORS on the query API — add it if a browser frontend needs to call it
  directly instead of through a backend.
- AviationStack's free/lower tiers cap `limit` at 100 and may not expose all
  fields used here (e.g., `fleet_size` on `/airlines`) — check your plan.
