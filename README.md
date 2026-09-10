# CoA bug-report service

Small Python service for Railway. It accepts a bug report, creates an issue in
**jealous-sound/azerothcore-wotlk-coa**, and returns its link.

**No volume, database, or GitHub duplicate searches.** Identical submissions are
filtered in memory, including simultaneous clicks. GitHub receives only the
create-issue request.

## Railway setup

1. Create a service from this private GitHub repository. Give Railway's GitHub
   integration access to `jealous-sound/coa-bug-report`.
2. Set these service variables:

   | Variable | Value |
   | --- | --- |
   | `GITHUB_TOKEN` | GitHub token with **Issues: read and write** on **azerothcore-wotlk-coa** |
   | `REPORT_API_KEY` | A **different**, randomly generated secret of at least 32 characters |
   | `GITHUB_REPOSITORY` | `jealous-sound/azerothcore-wotlk-coa` (also the default) |

3. Set **Healthcheck Path** to `/health`. Deploy with **one replica**. Railway
   detects the Dockerfile; leave build/start command overrides empty. The
   included Gunicorn configuration listens on Railway's `PORT` automatically.
4. Under Networking, **Generate Domain**. Supply that HTTPS base URL and the
   separate `REPORT_API_KEY` to the game-server report relay.

The token used to clone/push this source repository is not automatically a token
for creating issues in the target repository. Configure the target permission
explicitly. No credential is bundled in the source or image. `.env.example` is
only documentation; the service reads Railway environment variables directly.

This repository is the central service. The existing CoA relay will be connected
to its HTTPS endpoint after the Railway URL is available.

Optional variables: `MAX_REPORTS_PER_HOUR=60` and
`MIN_REPORT_INTERVAL_SECONDS=5`. These are global in-memory limits on creation
attempts, including rejected attempts; they reset when the process restarts.
Cached duplicates do not consume those limits or call GitHub.

Railway references: [Dockerfiles](https://docs.railway.com/builds/dockerfiles),
[healthchecks and PORT](https://docs.railway.com/deployments/healthchecks).

## API

`GET /health` is public and returns `{"status":"ok"}`. It confirms the service
started with its configuration; it does not check GitHub permissions.

Submit a report:

```http
POST /v1/reports
Authorization: Bearer <REPORT_API_KEY>
Content-Type: application/json

{
  "title": "Quest objective does not update",
  "body": "Steps:\n1. Accept the quest.\n2. Defeat the target.\n\nExpected: counter increases.\nActual: it stays at zero."
}
```

Title: 3-200 UTF-8 bytes. Body: 1-16,000 UTF-8 bytes. Request limit: 128 KiB.
Optional `report_id` accepts 16-64 ASCII letters, digits, underscores or hyphens
and is echoed in the response. It does not affect duplicate detection. No other
fields are accepted; add gameplay context to `body`.

Success (`201` for a new issue, `200` for a cached duplicate):

```json
{
  "status": "created",
  "duplicate": false,
  "issue_number": 123,
  "issue_url": "https://github.com/jealous-sound/azerothcore-wotlk-coa/issues/123"
}
```

A duplicate returns `"duplicate":true` with the existing issue number and URL.
If an identical report is still sending, the service returns `202`,
`{"status":"pending","retry_after":2}` and `Retry-After: 2`. Submit the same
content again after that delay to receive its result; there is no status endpoint.

| Response | Meaning |
| --- | --- |
| `400`, `413`, `415` | Invalid fields, size, or content type |
| `401` | Missing or incorrect intake API key |
| `422` | GitHub rejected the report |
| `429` | Rate limited; respect `Retry-After` |
| `503`, `github_access_denied` | Fix the Railway token, repo, Issues setting, or permissions |
| `502`, `github_result_unknown` | GitHub may have created the issue; do not blindly resubmit |
| Other `503` or lost connection | Delivery may be unknown; keep the original report for inspection |

## Duplicate filtering

The service hashes the complete title and body, ignoring `report_id`. Leading
and trailing whitespace is stripped, and body CRLF newlines become LF. Otherwise
content must match exactly; similar reports with different text remain separate.
This includes any gameplay context supplied in the body.

A lock admits only one simultaneous request for the same content. Completed
results stay in memory for **24 hours**, up to **4,096 entries**. Repeated clicks
return that result without another GitHub request. After an ambiguous GitHub
failure, repeated matching requests return `unknown` while cached, rather than
creating another issue.

The cache is lost on restart/redeploy. Identical reports can create another issue
after restart, expiry, or eviction. No historical GitHub checks are performed.
Use one replica and retain the supplied single-worker Gunicorn configuration so
all requests share the same cache.

## Credentials and logs

`REPORT_API_KEY` grants only report submission through this API. A key bundled
with distributed server packages can be extracted by recipients, but does not
expose the GitHub token or grant GitHub edit/delete permissions. Global quotas
still apply. Keep `GITHUB_TOKEN` private to Railway and use HTTPS externally.

The destination repo is set by the operator, never by the incoming request.
User `@mentions` are neutralized. Application logs omit report bodies, credentials,
and upstream error bodies. There is no background delivery worker or disk storage.

## Initial validation status

Local tests and local service startup were skipped at the owner's request.
Deployment health and a real report will be checked on Railway after setup.
No live issue was created during repository preparation.
