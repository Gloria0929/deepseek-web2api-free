# Responses API: initial supported subset

`POST /v1/responses`, `GET /v1/responses/{id}` and `DELETE /v1/responses/{id}` accept the standard OpenAI SDK. No custom headers or private request parameters are required. This implements the subset available through the DeepSeek web upstream.

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="your-proxy-api-key")
first = client.responses.create(model="deepseek-chat", input="Remember CEDAR-904.")
print(first.output_text)
with client.responses.stream(model="deepseek-chat", previous_response_id=first.id,
                             input="What code did I give you?") as stream:
    for event in stream:
        if event.type == "response.output_text.delta":
            print(event.delta, end="", flush=True)
    final = stream.get_final_response()
client.responses.retrieve(final.id)
client.responses.delete(final.id)
```

Inputs support strings, message Items (system/developer/user/assistant), text content, `input_image.image_url` (data URLs or public HTTP(S) URLs), function calls and function outputs. JPEG/PNG/WebP images use the shared validated upload pipeline and existing `IMAGE_*` limits. Function output content arrays may also contain images. OpenAI Files IDs are unsupported. Top-level `instructions` apply only to the current request; prior top-level instructions are not inherited. Explicit system/developer messages remain part of history.

Function tools use the flat Responses format with `type`, `name`, `description`, `parameters` and optional `strict`. Tool choice supports auto/none/required or a named function. DSML prompts implement calls; the proxy never executes client functions. At most one function call is allowed per turn. Submit results with `function_call_output.call_id`, not the output Item's `id`. Arguments must be JSON and are validated against the schema unless `strict=false`. Remote schema references are rejected. Invalid upstream calls fail with 502 or a terminal `response.failed` event. See the [Chinese guide](responses-api.md) for image and function examples.

State defaults to `store=true` and is held only in bounded process memory. `store=false` requires manual replay, including original image URLs/data URLs. Records are scoped to the caller's API key hash, or client IP without authentication. Retrieval/deletion use the same authentication and rate limits. Restarts lose state; separate workers do not share it.

Each response is an immutable snapshot. Every request replays its branch into a fresh upstream session on the original account, reusing account-bound file references without retaining image bytes or credentials. Branches do not mutate each other. A busy/unavailable parent account returns 503; changed credentials return 409. Retry or resend complete history and original images. Deleted, expired or evicted ancestors make dependent history unavailable, even if a descendant is still retrievable.

| Setting | Default |
|---|---:|
| `RESPONSES_TTL` (seconds) | 1800 |
| `RESPONSES_MAX_RECORDS` | 1000 |
| `RESPONSES_MAX_BYTES` | 33554432 |
| `RESPONSES_PER_CALLER` | 100 |
| `RESPONSES_MAX_DEPTH` | 128 |
| `RESPONSES_MAX_HISTORY_BYTES` | 4194304 |

Reconstructed history is also subject to the configured image count limit. Full replay costs more upstream text and latency. Usage is a local text estimate, including raw reasoning text, without accurate visual token accounting.

Streaming emits named Responses SSE events with monotonic sequence numbers, stable Item IDs and output/content indexes, including output text, reasoning text and function argument deltas. SDK stream aggregation is supported. Raw upstream reasoning is represented as `reasoning_text`, without invented summaries or encrypted state. Thinking and search follow server `THINKING`, `SEARCH` and `MODEL_ROUTES` policies.

Unsupported features return explicit errors: hosted tools, background processing, conversations, parallel function calls, native reasoning controls/encrypted reasoning, structured output formats, include, automatic truncation, explicit output token budgets, temperature and top_p. Conversations API, background cancellation, WebSockets, audio and PDF are outside this phase.
