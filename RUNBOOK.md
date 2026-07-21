# Runbook

Operational procedures for the Aviation Data Pipeline. See
[README.md](README.md) for architecture and API reference.

## Deploying / updating infrastructure

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

Lambda code changes are picked up automatically on `apply` — each function's
`archive_file` data source hashes its source directory, so a code-only
change still triggers a redeploy without touching `terraform.tfvars`.

**The AviationStack API key lands in Terraform state in plaintext**
(`aws_secretsmanager_secret_version`). For a real production account:
1. Run `terraform apply` once to create the secret (any placeholder value).
2. Immediately rotate it: `aws secretsmanager put-secret-value --secret-id <arn> --secret-string '{"api_key":"<real key>"}'`.
3. Treat `terraform.tfstate` as sensitive regardless (remote state with
   encryption + restricted access is assumed, not set up here).

## Verifying a new entity's schema (before enabling `routes` / `airplanes`)

1. `AVIATIONSTACK_API_KEY=xxxx python scripts/inspect_endpoint.py routes`
   — prints the flattened field names of one live record.
2. Compare against `_routes_extract()` in
   `lambdas/common/python/aviation_common/config.py`. Fix field paths if
   they don't match (nested paths use `_get(record, "parent", "child")`).
3. Flip `"schema_verified": False` → `True` for that entity in
   `ENTITY_CONFIG`.
4. Add the entity name to the appropriate `entities` list in
   `terraform/eventbridge.tf` (the `reference_schedule` target's `input`).
5. `terraform apply`. The next daily reference-schedule run will pick it up.
6. Spot-check: `aws dynamodb get-item --table-name AviationMasterTable --key '{"PK":{"S":"ROUTE#CA4127"},"SK":{"S":"METADATA"}}'`

## Replaying a DLQ message

A message in `aviation-pipeline-{extract,transform,load}-dlq` means a Lambda
exhausted its retries on an async invocation. The alarm
`aviation-pipeline-{extract,transform,load}-dlq-not-empty` fires when this
happens.

1. Inspect the message: `aws sqs receive-message --queue-url <dlq-url>`.
   The body is the original Lambda event (for `transform`/`load`, this
   contains the S3 bucket/key that failed).
2. Check CloudWatch Logs for that function around the failure timestamp to
   understand *why* (`/aws/lambda/aviation-pipeline-<extract|raw-prepared|prepared-dynamodb>`).
3. Fix the root cause if it's code/data (e.g., a genuinely malformed raw
   file), then redrive:
   - For `raw_prepared`/`prepared_dynamodb` failures: the S3 object that
     triggered the failure is still in the bucket. Re-invoke the Lambda
     directly with the same event, or re-upload the object (a `copy-object`
     onto itself re-fires the S3 event notification).
   - For `read_end_points` failures: just wait for the next scheduled run,
     or invoke manually (see below) — extraction is idempotent (each run
     writes a new timestamped file; nothing is overwritten).
4. Delete the message from the DLQ once resolved: `aws sqs delete-message --queue-url <dlq-url> --receipt-handle <handle>`.

## Manual / backfill invocation of Lambda 1

```bash
aws lambda invoke --function-name aviation-pipeline-read-end-points \
  --payload '{"entities": ["flights"], "max_pages": 1}' \
  --cli-binary-format raw-in-base64-out out.json
```

Omit `"entities"` to fetch everything in `DEFAULT_FETCH_ENTITIES` (the
verified 6). `max_pages` overrides the per-schedule default for this one
invocation.

**On the free AviationStack plan (100 requests/month, total), every page
requested here is billed against the same pool the schedules draw from.**
The defaults (`flights_schedule_expression` = every 12h, `reference_schedule_expression`
= monthly, both at `max_pages = 1`) already use ~65/100 per month — see
`terraform/variables.tf`. Before running a manual invoke with a higher
`max_pages` (e.g. for a deeper historical pull), check
`https://aviationstack.com/dashboard` (or your account's usage page) for
remaining quota, or you'll starve the scheduled runs for the rest of the
month. This constraint disappears on a paid plan — raise the Terraform
variables instead of overriding per-invoke.

## Responding to alarms

| Alarm | Likely cause | First step |
|---|---|---|
| `*-errors` | Unhandled exception in that Lambda | Check its CloudWatch Logs for the structured error log line |
| `*-throttles` | Concurrent invocations hit the account/function limit | Check for a burst of S3 events (e.g., a backfill); consider reserved concurrency if this recurs |
| `*-dlq-not-empty` | An invocation failed after exhausting retries | See "Replaying a DLQ message" above |
| `dynamodb-throttles` | Unexpected under `PAY_PER_REQUEST` — likely a hot partition | Check which PK is being hit hardest (e.g., one airline dominating GSI1 traffic); consider a random suffix on that GSI1PK if it recurs |
| `query-api-5xx` | Query API Lambda erroring | Check `/aws/lambda/aviation-pipeline-query-api` logs; often a DynamoDB permission or table-not-found issue after a manual change |

All alarms publish to the `aviation-pipeline-alerts` SNS topic
(`terraform output sns_alerts_topic_arn`). Subscribe additional endpoints
(Slack via SNS→Lambda, PagerDuty, etc.) there.

## Rotating the chatbot API key

```bash
terraform taint aws_api_gateway_api_key.chatbot
terraform apply
```

Distribute the new key (`terraform output query_api_key_id`, then
`aws apigateway get-api-key --api-key <id> --include-value`) to whatever
calls the query API. The old key stops working the moment `apply` completes
— coordinate the swap if this is a live integration.

## Adding a brand-new entity from scratch

1. Add an `_<entity>_extract()` and (if not just `METADATA`) a
   `_<entity>_key_fn()` to `config.py`, plus an `ENTITY_CONFIG` entry with
   `schema_verified: False`.
2. Run `scripts/inspect_endpoint.py <entity>`, fix the mapping, verify.
3. Add a Glue table entry to `terraform/athena.tf`'s
   `local.athena_entity_columns` if it should be queryable via Athena.
4. Add an API Gateway resource to `terraform/api_gateway.tf`'s
   `local.single_lookup_resources` map if it should be queryable via the
   chatbot API (only needed if the entity's lookup key differs from the
   generic `{id}` pattern already there).
5. Add it to an EventBridge schedule's `entities` list, `terraform apply`.
