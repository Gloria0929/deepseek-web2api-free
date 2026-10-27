# Responses API Implementation Plan

> Execute inline with executing-plans and TDD; final independent read-only review.

**Goal:** Standard OpenAI SDK Responses support for text/images, streaming, functions and snapshot-based state.
**Spec:** [Approved design](../specs/2026-10-27-responses-api.md).
**Architecture:** Protocol-independent ChatService feeds typed results/events into Responses formatting and SSE. ResponseStore holds immutable, caller-scoped deltas and account-bound file references. Routes compose injected runtime dependencies, never call chat/completions over HTTP.
**Tech:** FastAPI, existing DeepSeek adapter, jsonschema for function validation; OpenAI SDK for acceptance.

## Constraints

- Preserve 195 existing regressions. No changes to released v4.0.0 tag or automatic publishing.
- Memory-only state: default TTL 1800s, 1000 records, 32MiB serialized total, 100 records/caller, 128 ancestor depth, 4MiB reconstructed history. Store delta records, not duplicated full chains.
- Bind image state to caller/account/credentials; pin parent account for replay and error if unavailable. Fresh upstream sessions for immutable branches and non-inherited top-level instructions.
- Unsupported hosted tools, encrypted reasoning, background/conversation APIs, structured output guarantees, parallel tools and explicit output budgets must be rejected clearly.
- Existing body/image limits cover /v1/responses. Streams have stable IDs/indices/sequence and correct completion/failure, no fake completed on cancellation.

## Review focus and tasks

- [x] 1. Shared service/types: tests first; extract prompt building, raw preparation/invocation and cancel-safe stream ownership. Four old paths use service primitives; full baseline stays green.
- [x] 2. Input/format: tests for text/image Items, instructions replacement, function call IDs, strict schema and explicit unsupported fields; implement responses_format.py and requirements.
- [x] 3. Store: tests for caller isolation, TTL/byte/count/depth limits, immutable branches, store=false and parent deletion/expiry; implement response_store.py.
- [x] 4. SSE/routes: tests through SDK create/stream aggregation, retrieve/delete, tool result loop, image references, failure/cancellation, auth/body bounds; implement responses_stream.py, responses_routes.py and application wiring.
- [x] 5. Full pytest/smoke/e2e, real SDK/upstream text/image/branch/function checks, bilingual docs and capability ledger.
- [x] 6. Independent review; material fixes get failing regression then full verification. Report local branch and remaining limitations; no push requested.

## Ledger

User approved scope and authorized implementation using SuperPower. Inline execution preserves existing workflow. Branch feature/responses-api starts at 6504f99. Plan makes supported-subset decisions explicit rather than claiming the entire hosted OpenAI service.

Pre-flight: task 1 produces ChatService and cancellation-safe ownership consumed by tasks 2/4; task 2 canonical messages are consumed by task 3 snapshots and task 4 replay. Image normalization must strip image bytes before store writes. Task 3 snapshot deltas never include top-level instructions. Task 4 uses the same account lifecycle as old routes.

Ruling: keep the ledger in this tracked plan — the installed executing-plans skill refers to absent subagent-driven-development/scripts helpers; no skill workspace could be created by that helper. This preserves a reviewable execution record without depending on missing infrastructure.

Ruling: no version bump, push or release in this implementation task — the released v4.0.0 remains intact; development-branch additions are labeled in docs. Publication needs a separate user request.

Task 1: complete. Shared-service tests first, 197 tests green after extraction (195 existing regressions + 2 new). No protocol-specific HTTP imports in shared service.

Tasks 2/3: complete. Format/store tests first, 16 tests green including SDK Response parsing, function schemas, immutable branches, caller scope, expiry and count/byte/depth bounds.

Task 4: complete. SDK route tests observed 404 failures before wiring; then create/stream aggregation, retrieve/delete, branch/instruction replacement and image reference reuse passed. Function-loop tests exposed missing parameter schemas and truncated tool results; fixed with Responses-only schema prompting and untruncated tool results while preserving old defaults. Additional cancellation tests confirm no snapshot before completion and active worker draining before account release. Nested tool type tests observed four failures, fixed explicit client validation.

Verification 2026-10-27: full pytest 226 passed; UTF-8 smoke 7 groups passed. Initial smoke output hit Windows GBK UnicodeEncodeError, rerun with python -X utf8 succeeded. No product change needed.

Live SDK acceptance: OpenAI 3.39.0 strict response validation, temporary HAR credentials, 8 checks passed: text ACK, previous-response code recall, image ACK, streamed hidden-image recall with one upload and a replayed file ref, forced function/schema arguments, function output loop, retrieval, independent fresh upstream sessions. Earlier attempts saw an upstream file processing rejection; a diagnostic rerun observed PENDING/PARSING/SUCCESS and passed unchanged. Do not treat transient upstream behavior as an implementation fix. Credential-free report: ../validation/responses-sdk-2026-10-27.json (outside repository).

Task 6 review: independent read-only reviewer /root/review_responses identified three P2 issues: non-string named tool choice, dangling local schema references, and missing ancestors during snapshot publication. Five regression cases observed failing, then green after fixes. A sixth failing regression extended the same storage fix to prospective history depth/bytes. Reviewer independently verified all fixes (focused 24 tests passed), with no remaining findings in that scope. No implementer delegation.

Post-review verification: full pytest 232 passed; updated smoke includes Responses routes and passes all 7 groups; existing e2e passes all 3 scenarios. git diff --check reports no whitespace errors. The known local Windows LF/CRLF notices are informational.

Final live run: 10 checks passed, including immutable branches and adding a second image after streamed hidden-image recall. Comparison output: "First image: red, circle, one. Second image: blue, squares, two." Exactly 2 uploads and 2 refs on the comparison request. Every successful chat request used a fresh upstream session. Prior extended run hit file status FAILED/error_code 50404 on the second image; final run succeeded, with no product retry masking errors. The external verification script records bounded manual image retries if required. No credentials enter source/docs/report.

Publication follow-up: user explicitly requested committing to main and pushing origin. This supersedes the earlier no-push scope ruling. Remove development-branch wording from current capability docs; keep version/tag unchanged because no release was requested.
