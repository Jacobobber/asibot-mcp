# Asibot MCP Server -- Load Tests

Load testing suite using [Locust](https://locust.io/) to validate the Asibot MCP server under realistic production traffic (1000+ concurrent users).

## Quick start (automated, recommended)

The automated runner handles the full lifecycle: Docker stack, user seeding, Locust execution, result collection, and benchmark validation.

```bash
# From the project root:
./tests/load/run_loadtest.sh

# With custom parameters:
./tests/load/run_loadtest.sh --users 1000 --ramp 300 --sustain 300
```

**Requirements:** Docker and Docker Compose v2.

The script will:
1. Build and start PostgreSQL, PgBouncer, Asibot, and Locust containers
2. Wait for health checks to pass
3. Seed 200 test users with valid API keys and mock connector credentials
4. Run Locust with 1000 users (5-minute ramp-up, 5-minute sustained peak)
5. Collect CSV results, stats JSON, and container logs to `tests/load/results/<timestamp>/`
6. Print a pass/fail summary against performance benchmarks
7. Tear down the entire stack

## Manual setup

### Prerequisites

Install the load test dependencies:

```bash
pip install -e ".[dev]"
pip install locust>=2.20
```

### Step 1: Start the server

Using Docker Compose (production-like):
```bash
docker compose -f tests/load/docker-compose.loadtest.yml up -d postgres pgbouncer asibot
```

Or run locally:
```bash
ASIBOT_TRANSPORT=streamable-http ASIBOT_ALLOW_INSECURE_HTTP=true asibot
```

### Step 2: Seed test users

The seed script creates 200 users with valid API keys and mock connector credentials, then writes a CSV that Locust consumes:

```bash
# If using Docker stack (writes CSV into the container and the local mount):
docker compose -f tests/load/docker-compose.loadtest.yml run --rm seed

# If running locally (ensure ASIBOT_DATA_DIR matches the server):
python tests/load/seed_test_users.py --users 200 --output tests/load/test_users.csv
```

This creates `tests/load/test_users.csv` with columns `user_id,api_key`. Each user also gets mock credentials for GitHub, Atlassian, Confluence, Notion, Zendesk, HubSpot, Figma, and Salesforce connectors, so tool calls exercise the full authentication and permission path without hitting real external APIs.

### Step 3: Run Locust

#### Headless mode (CI/CD)

```bash
locust -f tests/load/locustfile.py --host http://localhost:8080 --headless \
  --users 1000 --spawn-rate 5 --run-time 10m \
  --csv tests/load/results/asibot_load
```

#### Web UI mode (interactive)

```bash
locust -f tests/load/locustfile.py --host http://localhost:8080
```

Then open http://localhost:8089 in your browser.

#### Using the built-in step load shape

The locustfile includes a `StepLoadShape` that automatically ramps from 10 to 1000 users over 5 minutes and sustains peak load for another 5 minutes:

```bash
locust -f tests/load/locustfile.py --host http://localhost:8080 --headless --run-time 11m
```

The ramp-up phases:

| Phase     | Duration | Users | Spawn Rate |
|-----------|----------|-------|------------|
| Warm-up   | 0-1 min  | 100   | 2/s        |
| Moderate  | 1-2 min  | 300   | 4/s        |
| Heavy     | 2-3 min  | 600   | 5/s        |
| Near peak | 3-4 min  | 900   | 5/s        |
| Full load | 4-5 min  | 1000  | 2/s        |
| Sustained | 5-10 min | 1000  | 1/s        |
| Cool-down | 10-11min | 0     | 20/s       |

#### Distributed mode (Docker Compose)

The `docker-compose.loadtest.yml` includes a Locust master and 2 workers. To add more workers:

```bash
docker compose -f tests/load/docker-compose.loadtest.yml up -d --scale locust-worker=4
```

#### Filtering by tag

Run only specific test scenarios:

```bash
# Only GitHub tool calls
locust -f tests/load/locustfile.py --host http://localhost:8080 --tags github

# Only auth/rate-limit scenarios
locust -f tests/load/locustfile.py --host http://localhost:8080 --tags auth rate_limit

# Only setup flow
locust -f tests/load/locustfile.py --host http://localhost:8080 --tags setup
```

## Test scenarios

### 1. Authentication (McpUser)
- **MCP initialize**: Opens an MCP session with Bearer token auth using pre-seeded API keys
- **Session reuse**: Verifies cached session IDs avoid repeated DB lookups
- **Weight**: 10x more traffic than unauthenticated users

### 2. Tool calls (McpUser)
- **asibot_health**: Health check (no auth required)
- **asibot_whoami**: Identity verification
- **asibot_services**: List connected services
- **github_search_repos**: GitHub repo search with varied queries
- **github_search_code**: Code search across repos
- **github_list_issues**: Issue listing with state filters
- **github_list_repos / list_commits / get_workflow_runs**: Other GitHub tools
- **jira_search / confluence_search**: Atlassian tool calls
- **asibot_connect**: Service connection flow

### 3. Setup flow
- **asibot_setup**: Triggers the device code OAuth flow (rate limited)
- **asibot_setup_status**: Polls setup completion with random IDs

### 4. Rate limiting
- **Burst requests**: 15 rapid health checks with no think time
- **Brute force auth**: 12 rapid requests with random bad API keys
- **Validates**: Server returns 429 or MCP-level rate limit errors

### 5. Unauthenticated traffic (UnauthenticatedUser)
- No API key, invalid API key, and malformed key attempts
- Verifies the server does not crash or leak resources under bad auth load

## How to read results

### CLI output

Locust prints a summary table at the end:

```
Name                           # reqs  # fails  Avg  Min  Max  Median  req/s  ...
-----------------------------  ------  -------  ---  ---  ---  ------  -----
MCP initialize (auth)            500    0(0%)   45    12  320    38    8.33
tool_call: asibot_health        2100    0(0%)   22     5  180    18   35.00
tool_call: github_search_repos  1500    5(0%)   85    20  950    65   25.00
...
```

Key columns:
- **# fails**: Non-zero means the server is returning errors under load
- **Avg / p50 / p95 / p99**: Latency distribution -- watch p95 and p99
- **req/s**: Throughput -- should stay stable at peak load

### Custom metrics and benchmark validation

At test end, a summary of custom metrics is printed, followed by automated benchmark checks:

```
CUSTOM METRICS SUMMARY
======================================================================
  auth_latency: count=500  avg=45.2ms  p50=38.0ms  p95=120.0ms  p99=250.0ms
  tool_call_latency: count=8000  avg=55.1ms  p50=42.0ms  p95=180.0ms  p99=400.0ms
  rate_limit_hits: count=150  avg=5.2ms  p50=4.0ms  p95=12.0ms  p99=25.0ms
======================================================================

BENCHMARK VALIDATION
======================================================================
  [PASS] Throughput >= 500 req/s: 523.4 req/s
  [PASS] Auth latency p95 < 100ms: 85.2ms
  [PASS] Tool call latency p95 < 200ms: 178.3ms
  [PASS] Error rate < 1%: 0.06%
======================================================================
RESULT: ALL BENCHMARKS PASSED
======================================================================
```

### CSV export

The automated runner saves results to `tests/load/results/<timestamp>/`:

```
results/
  20260318_143000/
    requests_stats.csv    # Per-endpoint statistics
    failures_stats.csv    # Failure details
    exceptions.csv        # Exception traces
    stats.json            # Full Locust stats snapshot
    asibot.log            # Server container logs
    locust-master.log     # Locust output (includes benchmark checks)
    postgres.log          # Database logs
```

## Target benchmarks for 1000 concurrent users

These are the performance targets the Asibot MCP server should meet under sustained load of 1000 concurrent users:

| Metric                     | Target           | Failure Threshold  |
|----------------------------|------------------|--------------------|
| **Throughput (RPS)**       | >= 500 req/s     | < 200 req/s        |
| **Auth latency (p95)**     | < 100ms          | > 500ms            |
| **Auth latency (p99)**     | < 250ms          | > 1000ms           |
| **Tool call latency (p95)**| < 200ms          | > 1000ms           |
| **Tool call latency (p99)**| < 500ms          | > 2000ms           |
| **Error rate**             | < 1%             | > 5%               |
| **Rate limit response**    | < 10ms (p95)     | > 50ms             |
| **Session cache hit rate** | > 90%            | < 70%              |
| **Memory growth**          | < 500MB at peak  | > 2GB              |
| **Connection pool usage**  | < 80% capacity   | 100% (exhausted)   |

### How to validate benchmarks

1. **Automated**: Run `./tests/load/run_loadtest.sh` -- the script prints a PASS/FAIL summary.
2. **From Locust output**: The `BENCHMARK VALIDATION` section at test end shows pass/fail for throughput, auth latency p95, tool call latency p95, and error rate.
3. **Manual inspection**: Check the CSV results in `tests/load/results/` for per-endpoint breakdowns.
4. **Memory**: Monitor with `docker stats` during the test.
5. **Connections**: Check PostgreSQL active connections with `SELECT count(*) FROM pg_stat_activity;`.

### Interpreting failures

- **High auth latency**: Database connection pool may be saturated. Check `pg_pool_max_size`.
- **High tool call latency**: External API rate limits or circuit breakers tripping. Check connector logs.
- **Growing error rate**: Server may be running out of memory or file descriptors. Check `ulimit -n`.
- **No rate limit hits in burst test**: Rate limiting may be misconfigured. Check `global_rate_limit_default` and `per_user_rate_limit_default` in settings.
- **Session cache miss rate high**: Session TTL may be too short or cache size too small. Check `session_cache_size` and `session_ttl` settings.

## Architecture

```
docker-compose.loadtest.yml
  |
  |-- postgres (16-alpine, tuned for load testing)
  |-- pgbouncer (transaction pooling, 200 max client connections)
  |-- asibot (HTTP transport, 2 CPU / 4GB RAM limit)
  |-- seed (one-shot: creates 200 users + mock connector creds)
  |-- locust-master (coordinates workers, serves web UI on :8089)
  |-- locust-worker-1 (generates load)
  |-- locust-worker-2 (generates load)
```

The seed script (`seed_test_users.py`) creates users via the `asibot.auth` module and stores mock connector credentials via `asibot.token_store`, so Locust users authenticate with real API keys that the server recognizes. Tool calls exercise the full permission and credential lookup path without making external API calls.
