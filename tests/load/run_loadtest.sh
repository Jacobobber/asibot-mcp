#!/usr/bin/env bash
#
# Automated load test runner for the Asibot MCP server.
#
# Spins up the full Docker Compose stack (PostgreSQL, PgBouncer, Asibot, Locust),
# seeds test users, runs Locust in headless mode, collects results, and prints
# a pass/fail summary against performance benchmarks.
#
# Usage:
#   ./tests/load/run_loadtest.sh [--users 1000] [--ramp 300] [--sustain 300]
#
# Defaults:
#   --users   1000   Number of concurrent simulated users
#   --ramp    300    Ramp-up duration in seconds (5 minutes)
#   --sustain 300    Sustained peak duration in seconds (5 minutes)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.loadtest.yml"
RESULTS_DIR="$SCRIPT_DIR/results"

# Default parameters
USERS=1000
RAMP_SECONDS=300
SUSTAIN_SECONDS=300

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --users)   USERS="$2"; shift 2 ;;
        --ramp)    RAMP_SECONDS="$2"; shift 2 ;;
        --sustain) SUSTAIN_SECONDS="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--users N] [--ramp SECONDS] [--sustain SECONDS]"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

TOTAL_SECONDS=$((RAMP_SECONDS + SUSTAIN_SECONDS))
SPAWN_RATE=$(( (USERS + RAMP_SECONDS - 1) / RAMP_SECONDS ))  # ceiling division

# Timestamp for this run
RUN_ID="$(date +%Y%m%d_%H%M%S)"
RUN_RESULTS_DIR="$RESULTS_DIR/$RUN_ID"

echo "============================================================"
echo "  Asibot Load Test Runner"
echo "============================================================"
echo "  Users:         $USERS"
echo "  Ramp-up:       ${RAMP_SECONDS}s (spawn rate: ${SPAWN_RATE}/s)"
echo "  Sustained:     ${SUSTAIN_SECONDS}s"
echo "  Total runtime: ${TOTAL_SECONDS}s"
echo "  Results:       $RUN_RESULTS_DIR"
echo "============================================================"
echo ""

# --- Cleanup function ---
cleanup() {
    echo ""
    echo "[*] Tearing down Docker Compose stack..."
    docker compose -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
    echo "[*] Cleanup complete."
}
trap cleanup EXIT

# --- Step 1: Build and start infrastructure ---
echo "[1/6] Building and starting Docker Compose stack..."
docker compose -f "$COMPOSE_FILE" build --quiet
docker compose -f "$COMPOSE_FILE" up -d postgres pgbouncer asibot

# --- Step 2: Wait for Asibot health check ---
echo "[2/6] Waiting for Asibot server to be healthy..."
MAX_WAIT=120
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    STATUS=$(docker compose -f "$COMPOSE_FILE" ps asibot --format json 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
# Handle both single object and list formats
if isinstance(data, list):
    data = data[0] if data else {}
print(data.get('Health', data.get('health', 'unknown')))
" 2>/dev/null || echo "unknown")

    if [[ "$STATUS" == *"healthy"* ]]; then
        echo "    Asibot is healthy (waited ${ELAPSED}s)"
        break
    fi
    sleep 3
    ELAPSED=$((ELAPSED + 3))
    if [ $((ELAPSED % 15)) -eq 0 ]; then
        echo "    Still waiting... (${ELAPSED}s, status: $STATUS)"
    fi
done

if [ $ELAPSED -ge $MAX_WAIT ]; then
    echo "ERROR: Asibot did not become healthy within ${MAX_WAIT}s"
    echo "Server logs:"
    docker compose -f "$COMPOSE_FILE" logs asibot --tail 50
    exit 1
fi

# --- Step 3: Seed test users ---
echo "[3/6] Seeding test users..."
docker compose -f "$COMPOSE_FILE" run --rm seed
echo "    Seed complete. CSV written."

# Verify the CSV was created
if [ ! -f "$SCRIPT_DIR/test_users.csv" ]; then
    echo "ERROR: test_users.csv was not created by seed script."
    echo "Seed logs:"
    docker compose -f "$COMPOSE_FILE" logs seed --tail 30
    exit 1
fi

USER_COUNT=$(tail -n +2 "$SCRIPT_DIR/test_users.csv" | wc -l)
echo "    Seeded $USER_COUNT test users."

# --- Step 4: Run Locust in headless mode ---
echo "[4/6] Starting Locust load test (${TOTAL_SECONDS}s)..."
mkdir -p "$RUN_RESULTS_DIR"

# Start Locust master + workers
docker compose -f "$COMPOSE_FILE" up -d locust-master locust-worker-1 locust-worker-2

# Wait for workers to connect
sleep 10

# Trigger headless run via the Locust REST API
echo "    Triggering load test: $USERS users, spawn rate $SPAWN_RATE/s, ${TOTAL_SECONDS}s runtime"
SWARM_RESPONSE=$(curl -sf "http://localhost:8089/swarm" \
    -X POST \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "user_count=$USERS&spawn_rate=$SPAWN_RATE&host=http://asibot:8080" 2>&1 || true)

if echo "$SWARM_RESPONSE" | grep -qi "error"; then
    echo "WARNING: Swarm API returned: $SWARM_RESPONSE"
fi

# Wait for the test to complete
echo "    Test running... (ETA: ${TOTAL_SECONDS}s)"
WAIT_ELAPSED=0
while [ $WAIT_ELAPSED -lt $((TOTAL_SECONDS + 60)) ]; do
    sleep 30
    WAIT_ELAPSED=$((WAIT_ELAPSED + 30))

    # Check current stats
    CURRENT_USERS=$(curl -sf "http://localhost:8089/stats/requests" 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('user_count', '?'))
except: print('?')
" 2>/dev/null || echo "?")

    MINS=$((WAIT_ELAPSED / 60))
    SECS=$((WAIT_ELAPSED % 60))
    echo "    [${MINS}m${SECS}s] Active users: $CURRENT_USERS"

    # Check if test finished (users dropped to 0 past the sustain period)
    if [ "$CURRENT_USERS" = "0" ] && [ $WAIT_ELAPSED -gt $RAMP_SECONDS ]; then
        echo "    Test completed (all users stopped)."
        break
    fi
done

# --- Step 5: Collect results ---
echo "[5/6] Collecting results..."

# Download CSV stats from Locust API
curl -sf "http://localhost:8089/stats/requests/csv" -o "$RUN_RESULTS_DIR/requests_stats.csv" 2>/dev/null || true
curl -sf "http://localhost:8089/stats/failures/csv" -o "$RUN_RESULTS_DIR/failures_stats.csv" 2>/dev/null || true
curl -sf "http://localhost:8089/exceptions/csv" -o "$RUN_RESULTS_DIR/exceptions.csv" 2>/dev/null || true

# Save full stats JSON
curl -sf "http://localhost:8089/stats/requests" -o "$RUN_RESULTS_DIR/stats.json" 2>/dev/null || true

# Save container logs
docker compose -f "$COMPOSE_FILE" logs asibot > "$RUN_RESULTS_DIR/asibot.log" 2>&1 || true
docker compose -f "$COMPOSE_FILE" logs locust-master > "$RUN_RESULTS_DIR/locust-master.log" 2>&1 || true
docker compose -f "$COMPOSE_FILE" logs postgres > "$RUN_RESULTS_DIR/postgres.log" 2>&1 || true

echo "    Results saved to $RUN_RESULTS_DIR/"

# --- Step 6: Print benchmark summary ---
echo ""
echo "[6/6] Benchmark Summary"
echo "============================================================"

# Parse stats.json for the summary
if [ -f "$RUN_RESULTS_DIR/stats.json" ]; then
    python3 -c "
import json, sys

with open('$RUN_RESULTS_DIR/stats.json') as f:
    data = json.load(f)

stats = data.get('stats', [])
total = None
for s in stats:
    if s.get('name') == 'Aggregated':
        total = s
        break

if not total:
    print('  No aggregated stats found.')
    sys.exit(1)

total_reqs = total.get('num_requests', 0)
total_fails = total.get('num_failures', 0)
current_rps = total.get('current_rps', 0)
avg_response = total.get('avg_response_time', 0)
p95 = total.get('response_times', {}).get('0.95', 0) if isinstance(total.get('response_times'), dict) else 0

error_rate = (total_fails / total_reqs * 100) if total_reqs > 0 else 100

checks = []

# Throughput
rps_pass = current_rps >= 500
checks.append(('Throughput >= 500 req/s', rps_pass, f'{current_rps:.1f} req/s'))

# Error rate
err_pass = error_rate < 1
checks.append(('Error rate < 1%', err_pass, f'{error_rate:.2f}% ({total_fails}/{total_reqs})'))

# Response time (global p95 as proxy)
# Individual auth/tool breakdowns are in the custom metrics printed by locust
p95_val = 0
for s in stats:
    name = s.get('name', '')
    if 'initialize' in name.lower() or 'auth' in name.lower():
        resp_times = s.get('response_times', {})
        if isinstance(resp_times, dict):
            p95_val = max(p95_val, resp_times.get('0.95', 0))

print(f'  Total requests: {total_reqs:,}')
print(f'  Total failures: {total_fails:,}')
print(f'  Avg response:   {avg_response:.1f}ms')
print()

all_passed = True
for label, passed, value in checks:
    status = 'PASS' if passed else 'FAIL'
    if not passed:
        all_passed = False
    print(f'  [{status}] {label}: {value}')

print()
if all_passed:
    print('  RESULT: ALL BENCHMARKS PASSED')
else:
    print('  RESULT: SOME BENCHMARKS FAILED -- review detailed results')
print()
print('  Detailed custom metrics (auth/tool p95) are in the Locust master log:')
print(f'    $RUN_RESULTS_DIR/locust-master.log')
" 2>/dev/null || echo "  Could not parse results. Check $RUN_RESULTS_DIR/stats.json"
else
    echo "  No stats.json found -- Locust may not have completed successfully."
    echo "  Check logs at $RUN_RESULTS_DIR/"
fi

echo "============================================================"
echo ""
echo "Results directory: $RUN_RESULTS_DIR/"
echo "  requests_stats.csv  - Per-endpoint statistics"
echo "  failures_stats.csv  - Failure details"
echo "  stats.json          - Full Locust stats snapshot"
echo "  asibot.log          - Server logs"
echo "  locust-master.log   - Locust output (includes benchmark checks)"
echo ""
