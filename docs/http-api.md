# Action request HTTP API

The transcriber's default continuous mode exposes a small local HTTP API for submitting and inspecting action requests. The API only accepts and reads durable intent; bundle execution remains in the synchronous coordinator.

## Configuration

The API is enabled by default. Its settings can be overridden in `config.custom.toml`:

```toml
[http]
enabled = true
host = "127.0.0.1"
port = 8765
```

Then start the default continuous mode:

```bash
uv run transcriber
```

The packaged configuration listens on `127.0.0.1`. Set `host = "0.0.0.0"` to accept connections through other network interfaces. The container image applies that override automatically. The API currently has no authentication, so restrict access at the host, container, or network level.

## Submit a request

`POST /requests` accepts a request ID and typed action. Supplying an ID is recommended because retrying the same submission is then idempotent.

```bash
curl --request POST \
  --header "Content-Type: application/json" \
  --data '{
    "request_id": "0123456789abcdef0123456789abcdef",
    "action": {
      "type": "set_title",
      "bundle_id": "fedcba9876543210fedcba9876543210",
      "title": "Quarterly planning"
    }
  }' \
  http://127.0.0.1:8765/requests
```

A valid submission returns `202 Accepted`, the canonical pending request, and a `Location` header.

Supported action payloads:

```json
{"type":"delete","bundle_id":"fedcba9876543210fedcba9876543210"}
```

```json
{
  "type":"set_title",
  "bundle_id":"fedcba9876543210fedcba9876543210",
  "title":"Quarterly planning"
}
```

```json
{
  "type":"merge",
  "source_bundle_id":"0123456789abcdef0123456789abcdef",
  "target":{"type":"previous"}
}
```

An explicit merge target uses:

```json
{
  "type":"merge",
  "source_bundle_id":"0123456789abcdef0123456789abcdef",
  "target":{
    "type":"bundle_id",
    "bundle_id":"fedcba9876543210fedcba9876543210"
  }
}
```

## Read status

```bash
curl --request GET \
  http://127.0.0.1:8765/requests/0123456789abcdef0123456789abcdef
```

The response is the canonical action request, including status, timestamps, origin, attempt count, and any terminal error.

## Health check

```bash
curl --request GET http://127.0.0.1:8765/health
```

FastAPI also provides interactive OpenAPI documentation at `http://127.0.0.1:8765/docs` and the generated schema at `http://127.0.0.1:8765/openapi.json`.

## Initial API limits

- No request listing, cancellation, authentication, or automatic retry endpoint.
- Requests in `succeeded`, `failed`, or `blocked` state are retained for 30 days.
- HTTP submission can occur while a job is running, but the new request executes only after the coordinator regains control.
