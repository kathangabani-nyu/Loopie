# Store Notes

Future folder for Redis clients and state stores.

## Intended Files

```text
redis_client.py
memory_store.py
routing_store.py
stream_store.py
```

## Redis Streams

Use these stream names:

```text
swarm:events
corrections:events
evals:events
```

Every event should eventually include:

```json
{
  "run_id": "run_001",
  "case_id": "refund_007",
  "agent": "memory_agent",
  "event_type": "memory_retrieved",
  "timestamp": "...",
  "payload": "{...}"
}
```

