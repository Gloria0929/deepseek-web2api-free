# Multimodal input: design and verification

Implementation and verification date: 2026-10-26. This document describes the code shipped with this change; it is not a claim about a future upstream API or a new release tag. Request examples and configurable limits are in [README](../README.md#图片输入与多模态) and [English README](../README_en.md#image-input).

## Supported contract

- OpenAI `/v1/chat/completions`: user `text` and `image_url` content parts. Image URLs can be base64 data URLs or public HTTP(S) URLs. `detail` has no upstream mapping.
- Anthropic `/v1/messages`: user `image.source` with base64 or URL sources, including nested images in user `tool_result.content`.
- Static JPEG, PNG and WebP; streaming and nonstreaming responses, with thinking on or off. Continue using `deepseek-chat` / upstream `model_type=default`.
- System/assistant image blocks are rejected. Animation, PDF/audio/video and public Files API ID input are outside this implementation.

## Module boundaries

| Module | Responsibility |
| --- | --- |
| `image_inputs.py` | Internal assets, limits, ordered markers and normalization; no HTTP framework dependency |
| `image_sources.py` | Base64/data URL decoding, MIME/pixel checks, credential-free URL fetch with DNS/address/redirect constraints |
| `deepseek_files.py` | Account transport, upload-specific PoW, multipart upload and file-processing polling |
| `turn_preparation.py` | Shared account-bound preparation and current/retry prompt/reference sets |
| `image_http.py` | Bounded JSON request buffering and protocol-specific image errors |
| Protocol handlers | Convert request schema to normalized input and format existing text/reasoning/tool responses |

The files client shares the selected account's authenticated transport. Remote image-source requests use a separate unauthenticated connection and no environment proxy. File upload uses a fresh challenge with target `/api/v0/file/upload_file`; its multipart form has one `file` part and no copied JSON Content-Type. Processing queries use repeated parameters, `file_ids=idA&file_ids=idB`; comma-separated IDs were rejected in the live probe.

## State and resource ownership

Image hashes are included in prompt markers so changed or reordered images affect history comparison. The existing explicit caller-scoped conversation key and account binding isolate caches; credential changes invalidate reuse. Up to 64 references, without image bytes, are retained per cached conversation until session eviction/expiry. With no explicit conversation identifier, references are not retained between requests.

Only newly needed files are uploaded within a reusable conversation. Current references follow attachment order; fresh-session retries carry the available full prompt and references. A latest-message-only client cannot reconstruct missing original image bytes after account changes; it must resend complete image history for that case. Neither file lifetime nor long-term retention is guaranteed by this local cache.

Upload/poll preparation finishes before SSE headers. Blocking work runs off the event loop. Cancellation during preparation leaves cleanup with the worker until handoff; cancellation during an active stream read drains that read before closing. Both protocol loops explicitly close their upstream iterators, and the response closes its body before account release even when a downstream send is cancelled.

Image limits are service resource policies, not asserted upstream maxima. The processing setting applies to each upload HTTP request and the processing poll deadline, not the entire request including PoW, account wait, retries and completion. Remote DNS work is limited to four outstanding resolver workers, each caller has a bounded DNS wait, checked addresses are pinned for connection, and every redirect is revalidated.

## Evidence and limits

- Full offline suite at implementation completion: **195 tests passed**, including the existing 135 and 60 added cases.
- Smoke: seven groups passed. Offline e2e: three scenarios passed. Dependency consistency and diff whitespace checks passed.
- Seven live downstream cases passed: OpenAI PNG nonstream, JPEG thinking stream, parent-chain text follow-up with zero reuploads, Anthropic WebP thinking nonstream, PNG thinking stream, nested tool-result image, and ordered two-image OpenAI stream.
- Final stream cleanup changes were followed by successful real thinking-stream checks on both protocols, including visible answers, reasoning and normal termination.
- Upstream probes established same-account file-ID reuse across several new sessions, correct two-image order and reversal, and successful related follow-up without repeating references. Such follow-up may use previous answer text; it does not prove retention of all unmentioned visual details.
- Real public-URL success remains **unverified in the validation environment**: its DNS resolved the image host to `198.18.0.180`, so both protocols returned 400 before upload as required by public-address policy. Fake-IP addresses are not bypassed. Use real public DNS for that host or base64/data URL input.
- OpenAI nonstream usage is a local text/marker estimate; streaming emits no usage chunk, and Anthropic usage remains `-1`. No accurate visual-token accounting is claimed.
- No high-concurrency or long-duration stability claim, exact upstream size/count limit, or long-term file-ID lifetime is made.

Private HARs, tokens, cookies, signed image URLs and temporary acceptance credentials are excluded from the repository. Verification artifacts containing only sanitized results remain local; the summary above is the portable evidence record.
