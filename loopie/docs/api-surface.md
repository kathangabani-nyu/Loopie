# API Surface

The initial backend should eventually expose this minimal API.

```text
POST /seed
POST /run/baseline
POST /corrections/propose
POST /corrections/{id}/approve
POST /run/patched
GET  /runs/{run_id}
GET  /events/stream
GET  /state
```

## Endpoint Intent

`POST /seed`

Seeds Redis with intentionally flawed memory and routing artifacts.

`POST /run/baseline`

Runs the fixed eval cases against the unpatched swarm.

`POST /corrections/propose`

Classifies failures and creates structured correction proposals.

`POST /corrections/{id}/approve`

Applies an approved correction to Redis.

`POST /run/patched`

Reruns the same eval cases after correction approval.

`GET /runs/{run_id}`

Returns run state, scores, events, and correction links.

`GET /events/stream`

Streams event log updates to the UI.

`GET /state`

Returns current memory, routing, correction, and run state.

