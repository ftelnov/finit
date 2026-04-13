"""Seed OpenSearch with sample application logs for eval scenarios.

Run after OpenSearch is healthy:
    python seed_opensearch.py [--url http://localhost:9200]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone

import httpx

OPENSEARCH_URL = "http://localhost:9200"
INDEX = "app-logs-2026.04"

SAMPLE_SERVICES = ["api-gateway", "auth-service", "user-service", "payment-service"]
SAMPLE_LEVELS = ["INFO", "WARN", "ERROR", "DEBUG"]
SAMPLE_ERRORS = [
    ("ERROR", "NullPointerException in UserService.getUser()", "user-service"),
    ("ERROR", "Connection refused to postgres:5432", "auth-service"),
    ("ERROR", "Request timeout after 30s: GET /api/payments/status", "payment-service"),
    ("ERROR", "JWT token expired for user_id=u-12345", "auth-service"),
    ("ERROR", "Rate limit exceeded: 429 from upstream", "api-gateway"),
    ("WARN", "Slow query detected: SELECT * FROM users WHERE email LIKE '%@%' (2340ms)", "user-service"),
    ("WARN", "Connection pool exhausted, waiting for available connection", "auth-service"),
    ("WARN", "Retry attempt 3/5 for payment processing", "payment-service"),
    ("INFO", "Health check passed", "api-gateway"),
    ("INFO", "Request completed: GET /api/users/123 200 45ms", "user-service"),
    ("INFO", "Cache hit for session_id=sess-abc123", "auth-service"),
    ("DEBUG", "Parsing request body: content-length=1234", "api-gateway"),
]


def generate_logs(count: int = 500) -> list[dict]:
    """Generate realistic application log entries."""
    logs = []
    now = datetime.now(timezone.utc)

    for i in range(count):
        # Skew toward recent entries and more errors in the last hour
        hours_ago = random.expovariate(0.3)  # exponential: most entries are recent
        ts = now - timedelta(hours=min(hours_ago, 24))

        # Pick a log template (weighted: more errors in recent hours)
        if hours_ago < 1 and random.random() < 0.4:
            # Recent = more errors
            template = random.choice([t for t in SAMPLE_ERRORS if t[0] == "ERROR"])
        else:
            template = random.choice(SAMPLE_ERRORS)

        level, message, service = template

        # Add some variation
        if "user_id" in message:
            message = message.replace("u-12345", f"u-{random.randint(10000, 99999)}")
        if "session_id" in message:
            message = message.replace("sess-abc123", f"sess-{random.randbytes(4).hex()}")

        log_entry = {
            "@timestamp": ts.isoformat(),
            "level": level,
            "service": service,
            "message": message,
            "host": f"pod-{service}-{random.randint(1, 3)}",
            "trace_id": f"trace-{random.randbytes(8).hex()}",
            "request_id": f"req-{random.randbytes(4).hex()}",
        }
        logs.append(log_entry)

    return logs


def seed(url: str, logs: list[dict]) -> None:
    """Bulk-index logs into OpenSearch."""
    client = httpx.Client(timeout=30.0)

    # Create index with mapping
    mapping = {
        "mappings": {
            "properties": {
                "@timestamp": {"type": "date"},
                "level": {"type": "keyword"},
                "service": {"type": "keyword"},
                "message": {"type": "text"},
                "host": {"type": "keyword"},
                "trace_id": {"type": "keyword"},
                "request_id": {"type": "keyword"},
            }
        }
    }

    # Delete if exists
    client.delete(f"{url}/{INDEX}")
    resp = client.put(f"{url}/{INDEX}", json=mapping)
    resp.raise_for_status()
    print(f"Created index {INDEX}")

    # Bulk index
    bulk_body = ""
    for log in logs:
        bulk_body += json.dumps({"index": {"_index": INDEX}}) + "\n"
        bulk_body += json.dumps(log) + "\n"

    resp = client.post(
        f"{url}/_bulk",
        content=bulk_body,
        headers={"Content-Type": "application/x-ndjson"},
    )
    resp.raise_for_status()
    result = resp.json()
    errors = result.get("errors", False)
    items = len(result.get("items", []))
    print(f"Indexed {items} logs (errors={errors})")

    # Refresh
    client.post(f"{url}/{INDEX}/_refresh")

    # Verify
    count_resp = client.get(f"{url}/{INDEX}/_count")
    count = count_resp.json().get("count", 0)
    print(f"Total documents in {INDEX}: {count}")

    # Show error distribution
    agg_resp = client.post(f"{url}/{INDEX}/_search", json={
        "size": 0,
        "aggs": {
            "by_level": {"terms": {"field": "level"}},
            "by_service": {"terms": {"field": "service"}},
        }
    })
    aggs = agg_resp.json().get("aggregations", {})
    print("\nBy level:")
    for bucket in aggs.get("by_level", {}).get("buckets", []):
        print(f"  {bucket['key']}: {bucket['doc_count']}")
    print("\nBy service:")
    for bucket in aggs.get("by_service", {}).get("buckets", []):
        print(f"  {bucket['key']}: {bucket['doc_count']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=OPENSEARCH_URL)
    parser.add_argument("--count", type=int, default=500)
    args = parser.parse_args()

    logs = generate_logs(args.count)
    seed(args.url, logs)
