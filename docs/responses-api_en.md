# Responses API: supported subset

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

Function tools use the flat Responses format with `type`, `name`, `description`, `parameters` and optional `strict`. Tool choice supports auto/none/required or a named function. DSML prompts implement calls; the proxy never executes client functions. Submit results with `function_call_output.call_id`, not the output Item's `id`.

Explicit strict=true requires supported schemas to close every object with additionalProperties=false and require all defined properties. Use explicit null types for nullable values. Missing/null strict attempts normalization across objects/properties, items, anyOf and $defs, returning the effective schema and strict=true; incompatible valid schemas fall back with strict=false. Invalid schemas/references fail before account acquisition. Normalization does not add nullable types. strict=false skips output schema validation but still requires valid finite JSON. Local JSON Pointer references are supported; remote refs, resource scopes ($id), anchors and dynamic refs are rejected. Prompt and response.tools use identical effective definitions. Validation is after generation, not constrained upstream decoding.

For backward compatibility parallel_tool_calls defaults to false (one call). Explicit true permits up to 64 calls, including repeated function names with unique call IDs. Supply every pending result before requesting generation; reversed result order is accepted, missing/duplicate/unknown IDs return400. A parsed batch is validated before its function Items are emitted; later batch failures may leave partial output but must terminate failed without stored completion. Arguments deltas may follow complete DSML parsing rather than native token-level function generation. See the [Chinese guide](responses-api.md) for image and multi-call examples.

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

The request boundary consumes these known client hints without forwarding, logging or persisting them as conversation state:

| Field | Accepted values and behavior |
|---|---|
| `prompt_cache_key` | String/null, at most 512 characters; ignored optimization hint, never caller identity or session routing |
| `client_metadata` | JSON object/null, at most 16 KiB when UTF-8 JSON encoded; non-finite numbers rejected |
| `include` | Empty/null or only `reasoning.encrypted_content`; encrypted state is unavailable and omitted from output |
| `reasoning.effort` | null or `none/minimal/low/medium/high/xhigh/max/ultra`; server thinking policy still applies |
| `reasoning.summary` | null or `auto/concise/detailed/none`; no native summary guarantee, response `reasoning` remains null |
| `text.verbosity` | null or `low/medium/high`; no native length control, plain text format only |

Unknown fields/hints and other include values return 400 with the parameter identified. Hosted tools, tool namespaces, background processing, conversations, foreign encrypted reasoning input, structured output, automatic truncation, explicit output token budgets, temperature and top_p still return errors. Conversations API, background cancellation, WebSockets, audio and PDF are outside this phase.

### Codex CLI

Codex CLI 0.145.0 has been verified for initial streaming and ordinary function roundtrips with a Responses provider pointing to this service's `/v1`. Restrict tools to the supported subset by disabling web search, apps/plugins and multi-agent namespaces:

```toml
web_search = "disabled"
[features]
apps = false
plugins = false
multi_agent = false
```

Set the custom provider's `wire_api = "responses"`, `base_url` and `env_key`, with the environment variable containing this service's API key. Any configured MCP/external tools must also use supported flat function definitions; acceptance uses an empty MCP configuration. Client hints can be sent normally without an intermediate field-removal layer. This does not imply support for every Codex plugin or built-in tool.

## Client acceptance and diagnostics

Pinned Python SDK3.39.0 sync/async and JavaScript SDK7.45.0 run 10 required scenario groups each over actual loopback HTTP: text, raw/aggregate streams, images, state/replay, functions, HTTP errors, failed terminals, aborts and timeouts. Synthetic upstream fixtures retain real routes/pool/semaphore/parser/state and require no credentials.

```bash
pip install -r requirements.txt -r tests/responses_compat/requirements.txt
npm ci --prefix tests/responses_compat
python scripts/run_responses_compat.py --require-all
```

Missing Node or a driver fails the required CI job; ordinary pytest does not require Node. Real upstream adherence is validated separately.

`python scripts/verify_codex_responses.py --cli <Codex executable>` additionally runs the installed CLI in an owned temporary workspace with its read-only sandbox. It checks initial SSE, a synthetic-file read through a function call and result replay, `store=false`, and resource cleanup. Only the upstream is scripted; no HAR is used. This optional acceptance is separate from ordinary pytest and does not establish real upstream tool adherence.

For an authorized HAR, run `python scripts/verify_responses_live.py --har <capture.har> --report <report.json>`. Both Python and JS run by default; select one with --client python or --client javascript. Credentials stay in a temporary account store; the report contains only check results, failure classes/statuses and anonymous transport counts. The verifier and SDK do not add retries; the proxy adapter still applies `DEEPSEEK_RATE_LIMIT_RETRY_DELAYS` to upstream rate limits and its existing empty-response retry policy. Final credential, upstream or model-adherence failures remain visible in the report. This sends real upstream requests and is excluded from CI.

Existing JSON logging emits responses_execution for history replay, account wait, session creation, image upload/processing, generation and storage. Streams additionally report first upstream event/visible text. Whitelisted fields contain internal IDs, fixed stages/statuses, durations, counts and safe error codes; no prompt/output, image URL/base64/file ID, tool data or credentials. Logging failure does not affect business cleanup.

Observed cancellation before state commit does not save a snapshot and drains the active worker before releasing resources. A disconnect after commit does not imply successful delivery; TCP delivery and snapshot commit are not atomic.
