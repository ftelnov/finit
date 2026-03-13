"""Shared memory bus client using Redis Streams."""

import json
import os
import time
import redis


def get_redis() -> redis.Redis:
    """Create a Redis client from environment."""
    url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    if not url.startswith("redis://"):
        url = f"redis://{url}"
    return redis.Redis.from_url(url, decode_responses=True)


def consume_stream(
    rdb: redis.Redis,
    stream: str,
    group: str,
    consumer: str,
    handler,
    block_ms: int = 2000,
):
    """Consume messages from a Redis stream using consumer groups.

    handler(task_data: dict) -> dict or None
        If handler returns a dict, it's treated as the updated task.
    """
    # Create consumer group if it doesn't exist.
    try:
        rdb.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise

    print(f"[{consumer}] Listening on stream={stream} group={group}")

    while True:
        try:
            results = rdb.xreadgroup(
                group, consumer, {stream: ">"}, count=1, block=block_ms
            )
            if not results:
                continue

            for stream_name, messages in results:
                for msg_id, msg_data in messages:
                    raw = msg_data.get("data", "{}")
                    try:
                        task = json.loads(raw)
                    except json.JSONDecodeError:
                        print(f"[{consumer}] Bad message: {raw[:200]}")
                        rdb.xack(stream, group, msg_id)
                        continue

                    print(f"[{consumer}] Processing task={task.get('id', '?')}")
                    try:
                        result = handler(task)
                        if result is not None:
                            # Result is published by the handler itself.
                            pass
                    except Exception as e:
                        print(f"[{consumer}] Error processing task: {e}")
                    finally:
                        rdb.xack(stream, group, msg_id)

        except redis.ConnectionError:
            print(f"[{consumer}] Redis connection lost, retrying in 2s...")
            time.sleep(2)
        except Exception as e:
            print(f"[{consumer}] Unexpected error: {e}")
            time.sleep(1)


def publish(rdb: redis.Redis, stream: str, task: dict):
    """Publish a task to a Redis stream."""
    rdb.xadd(stream, {"data": json.dumps(task, ensure_ascii=False)})
    print(f"[bus] Published to {stream}: task={task.get('id', '?')}")
