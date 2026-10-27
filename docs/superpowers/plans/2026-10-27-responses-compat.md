# Responses Compatibility and Tool Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended here) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify three standard SDK clients over real HTTP and strengthen Responses function/schema, error, cancellation and diagnostic behavior.
**Architecture:** Keep Responses as an injected HTTP adapter over ChatService. Extract tool policy and safe error classification, carry complete parsed call batches into SSE, and add optional protocol-neutral observation hooks. A test-only scripted upstream drives the actual HTTP/application/state paths for all clients.
**Tech Stack:** Python 3.10+, FastAPI/uvicorn, Pydantic, jsonschema/referencing, existing DSML parser/sieve, official Python/JS OpenAI SDKs, Node 20 CI, pytest.
**Spec:** [User-approved design](../specs/2026-10-27-responses-compat-design.md).

## Global Constraints

- Start from feature/responses-compat / a19a328, based on main 887501e. Preserve the 232-test baseline, existing smoke/e2e, Python 3.10/3.11/3.12 CI and all old protocol behavior except approved strict/default normalization changes within Responses.
- parallel_tool_calls defaults to false; explicitly true permits at most 64 calls/response. Proxy does not execute client functions. All pending call results must be supplied before generation.
- Strict schemas require every object's additionalProperties=false and all properties in required. Missing/null strict attempts supported normalization and otherwise returns strict=false; invalid schema/references are errors, never fallback.
- Keep existing state bounds: TTL1800, global1000 records/32MiB, per-caller100, depth128, history4MiB; caller/account/credential binding and fresh-session replay remain.
- No product test flags/endpoints. Test fixtures contain only synthetic content and IDs. No real upstream credentials in CI. JS test dependencies stay separate from WebUI.
- Observed cancellation before state commit drains workers, closes streams and releases accounts/slots without saving. A send failure after commit does not imply atomic delivery or rollback.
- No state persistence, compaction, token budget, structured text guarantees, hosted tools, background/conversation/WebSocket/PDF/audio work or version/tag changes. Push/release require user instruction; local task commits are part of execution.
- Fresh whole-suite evidence at each task gate; targeted RED must fail for missing behavior, followed by GREEN. Windows tools may require escalated execution for async/TestClient/network; python -X utf8 avoids smoke output encoding errors.

## Review Focus

- Multiple tool calls in one DSML batch with only the second invalid: no function Item from that batch emitted, one failed terminal, no stored completed snapshot (Task 3).
- Restored outputs contain historical completed calls plus an unresolved current batch: match by call_id, accept reversed complete results, reject duplicates/missing/unknown IDs (Tasks 2/5).
- Empty/null schemas and nested local refs, recursive resolution or non-finite argument JSON: preserve valid semantics, reject unsafe/invalid input before account acquisition or invalid upstream output as502 (Task 2).
- TCP abort/timeout before first event, during text and during a tool Item: no worker/slot leak; a following request succeeds without assuming exactly-once TCP delivery (Tasks 3/5).
- Logging raises or synthetic secrets appear in exceptions/inputs: observation cannot break success/cleanup and must never emit sensitive sentinel values (Task 4).

## File Map

- Add responses_tools.py; keep tool policy outside HTTP/SSE. Modify responses_format.py only for request/Item conversion and delegation.
- Extend responses_errors.py, responses_routes.py, responses_stream.py for shared error classification, field paths and batch composition.
- Extend chat_types.py/chat_service.py using opt-in complete tool batches; preserve default event consumers.
- Add execution_observer.py (generic safe phase hook) and responses_observability.py (request trace/logger adapter). Add optional hooks to turn_preparation.py and composition in responses_routes.py; shared modules never import Responses.
- Add tests/test_responses_errors.py, test_responses_tools.py, test_responses_observability.py; extend existing service/format/API/cancellation tests.
- Add tests/responses_compat/{__init__.py,backend.py,server.py,server_process.py,scenarios.json,fixtures/}; SDK drivers python_sync.py,python_async.py,javascript.mjs plus isolated package.json/package-lock.json; add scripts/run_responses_compat.py and scripts/verify_responses_live.py.
- Add SDK CI job in .github/workflows/ci.yml; update .gitignore, docs/responses-api*.md, README*.md, AGENTS.md and test dependency lock inputs as needed.

### Task 1: Request and error semantics

**Files:** responses_errors.py, responses_format.py, responses_routes.py, responses_stream.py; tests/test_responses_errors.py, tests/test_responses_format.py, tests/test_responses_api.py.
**Interfaces:** preserve ResponseError(message,status=400,code=...,param=None). Add classify_error(error:Exception)->ResponseError and validation_error(error:pydantic.ValidationError)->ResponseError. Add ResponseError.stream_payload()->dict with SDK-valid failure code plus safe message/param/type; ResponseStream.fail(error:ResponseError)->str consumes it. Imports of adapter/HTTP-specific exceptions belong in error adapter, not tool policy.

- [x] Write test_classification_is_safe_and_consistent: HTTP request errors400, authentication401, rate429, context404/409, account503, upstream502; unsupported fields identify exact param. Sentinel strings in upstream exceptions and Pydantic input never appear. Stream codes follow SDK literals: rate_limit_exceeded, corresponding image category, invalid_prompt for client/context errors, server_error for other upstream/internal failures; preserve original safe HTTP classification in optional error.type/param. SDK strict validation must parse each failed Response. Assert pre-header failures are JSON, not SSE.
- [x] Write parameterized field matrix: model/instructions/previous_response_id accept null; metadata null becomes{}; tools/include null become[]; tool_choice null becomes auto; parallel_tool_calls/background null become false; truncation null becomes disabled; reasoning/text null stay unset. input/stream/store reject null and invalid types (including string/number booleans). Explicit unsupported controls remain400; never silently accept effective temperature/output budgets. Verify against official field definitions before implementing and ledger any necessary supported-subset ruling.
- [x] Run `.venv/Scripts/python -m pytest tests/test_responses_errors.py tests/test_responses_format.py tests/test_responses_api.py -q`; expected new assertions FAIL against current behavior, not collection/import typos.
- [x] Implement strict field handling, null normalization only for declared nullable options and safe Pydantic loc-to-param conversion. Keep normalized effective values identical in prompt and response. Delegate public_error to shared classification and change fail caller to pass classified error.
- [x] Run targeted tests then `.venv/Scripts/python -m pytest -q`; expected all PASS. Stage only this task's files, commit `fix: align Responses request and error semantics` and record evidence in ledger.

### Task 2: Tool policy, strict normalization and pending results

**Files:** add responses_tools.py and tests/test_responses_tools.py; modify responses_format.py and tests/test_responses_format.py.
**Interfaces:** normalize_tools(tools:list[dict])->list[dict] returns deep-copied effective definitions; validate_choice(tools:list[dict],choice:str|dict)->None; validate_call_batch(tools:list[dict],choice:str|dict,calls:list[dict],*,parallel:bool,complete:bool=True)->None; pending_calls(messages:list[dict])->dict[str,str]. Keep responses_format.validate_calls(request,calls,*,complete=True) as delegating compatibility wrapper.

- [x] Write tests for strict true invalid closure/required400 with tools[index].parameters; strict false JSON-only validation; missing/null strict normalizes nested object/items/anyOf/$defs without adding nullable types; incompatible valid schema falls back with strict=false; explicit true unsupported construct400. Assert effective definitions are copied and response.tools equals prompted definitions.
- [x] Write tests for parameters missing/null -> closed empty object, valid empty dict not replaced, invalid parameter type/remote/dangling local ref400, valid escaped JSON Pointer works, recursive refs terminate or fail safely without network, reject NaN/Infinity in function arguments. Use offline referencing.Registry for result validation.
- [x] Write call tests for false rejects2, true accepts up to64 and rejects65; duplicate/non-string IDs, unknown names, required empty, named mismatch, none calls, same-name distinct IDs. Write historical/manual replay tests accepting reversed complete results and rejecting missing, duplicate or unknown call_id with input[index].call_id. Final unresolved set must be empty before acquiring account.
- [x] Run `.venv/Scripts/python -m pytest tests/test_responses_tools.py tests/test_responses_format.py -q`; expected new policy assertions FAIL. Implement schema walk and output validation with explicit limits (existing tools64, schema262144 bytes, nesting64). Normalize only object/properties, arrays/items, anyOf, $defs and local refs; unsupported applicators/conditional/object-key constructs cause strict fallback or explicit strict400. Avoid treating arbitrary schema annotations as subschemas.
- [x] Delegate request tool validation and Item pending-call bookkeeping; keep canonical message and image attachment formats. Run focused then full pytest, expected all PASS; commit `feat: normalize Responses tools and validate result batches`.

### Task 3: Complete tool batches through streaming and cancellation

**Files:** chat_types.py, chat_service.py, responses_format.py, responses_routes.py, responses_stream.py; tests/test_chat_service.py, tests/test_responses_api.py, tests/test_multimodal_cancellation.py.
**Interfaces:** append ChatEvent.calls:list[dict]|None=None, preserving existing fields. ChatService.events(...,*,batch_tools:bool=False,on_token:Callable[[],None]|None=None) emits kind='tools' with complete parser batch only when opted in, preserving kind='tool'/call for default consumers. Routes validate cumulative calls using Task2 wrapper with complete=False before expanding each validated batch into individual tool events; validate complete=True at end.

- [x] Write test_service_preserves_batch_and_closes_source with split DSML, two invokes and a later batch. Assert opt-in batches keep order and default events remain unchanged. Write SDK create/stream aggregation tests for multiple calls, stable unique IDs/indexes, at most64 and parallel false error.
- [x] Write test_second_invalid_call_does_not_emit_first_in_same_batch: valid first, invalid second => no function_call added frame, one response.failed, no completed/store entry. Later independent invalid batch may leave prior emitted Items but still ends failed. Compare nonstream/stream semantic call sets, not random IDs.
- [x] Write SDK multi-result loop tests with reversed outputs, same-name functions, tool-result image and store=false replay. Assert unresolved batch never reaches adapter and branch/file/account isolation remains intact.
- [x] Extend observed cancellation tests around prepare, next() and send() with batch Items; assert close precedes release and slots can be reacquired. Run targeted tests expecting RED; remove the request validator's parallel=true rejection, echo request.parallel_tool_calls in response_object, implement batch prompt policy (remove unconditional single-call instruction only when parallel true), route composition and cleanup without copying chat_runtime ownership.
- [x] Run full pytest and existing `.venv/Scripts/python -X utf8 -m tests.smoke`; expected PASS. Commit `feat: support Responses multi-function streaming and result loops`.

### Task 4: Safe stage observation

**Files:** add execution_observer.py, responses_observability.py, tests/test_responses_observability.py; modify turn_preparation.py, chat_service.py and responses_routes.py; extend tests/test_image_turns.py.
**Interfaces:** frozen Observation(stage:str,elapsed_ms:float,status:str,counts:dict[str,int]); phase(observer:Callable[[Observation],None]|None,stage:str,*,clock=time.monotonic,**counts) is a context manager that never masks business exceptions and suppresses observer failures. RequestTrace(request_id:str,*,clock=time.monotonic,emit:Callable[[dict],None]|None=None) exposes observe(Observation), first(stage:str), finish(status:str,code:str|None=None), and bind(response_id:str). No global request buffers.

- [x] Write fake-clock assertions for history_replay/account_wait/session_create/image_upload/image_processing/generation/storage and once-only first_upstream_event/first_output_text. Missing/nonstream first-token values remain absent; cancelled differs from failed; emitted fields use safe whitelisted scalars only.
- [x] Write tests that emit raises during success and failure, cleanup/business result remain correct. Put sentinel secrets in prompt, URL, bytes, fileID, tool args/results and exception text; captured JSON must contain none. Diagnostics omit account credentials and caller keys entirely.
- [x] Run targeted tests expecting RED. Add optional observer keyword to prepare_turn defaultNone; instrument existing session/upload/wait operations using generic phase. RequestTrace composes acquire/history/generation/save phases in routes. Feed first-upstream marker via Task3 on_token callback; mark first visible text separately. Trace finish must execute once across errors/cancel and never release resources itself.
- [x] Run focused and full pytest, expected PASS; commit `feat: add safe Responses execution stage diagnostics`.

### Task 5: Three SDK drivers over real HTTP

**Files:** tests/responses_compat files listed above; scripts/run_responses_compat.py; tests/test_responses_compat_harness.py. No production test endpoints or test-specific request fields.
**Interfaces:** server_process.running_server()->context manager yielding base URL/key/test-state client; server module launches real server.app with test adapters through ordinary Python injection. Attach ScriptedAdapter to synthetic Account._adapter while retaining actual pool.acquire/release and semaphore. A test-only authenticated diagnostics route may exist only in the test module to observe counts/store state. Drivers consume shared scenarios.json and emit JSON results{name,passed,client,detail}; runner returns nonzero on any missing driver/failure.

- [x] Write harness test proving client sees HTTP status/SSE through loopback, then forced driver failure returns nonzero, timeout closes only owned process, and no external request occurs. Use ephemeral IPv4 loopback port, readiness with deadline, temporary account/key store and fixed test-only auth; never enumerate/kill unrelated processes. Run tests before implementing runner; expect RED.
- [x] Implement scripted fixtures for text/reasoning/split DSML/batch/later invalid batch/rate hint/midstream error/upload failure/slow first frame/slow text/slow tool. Use existing parser/sieve; test-only prompt scenario markers are ordinary text. Counters expose acquired/released/closed/upload counts and stored response count, no credentials.
- [x] Add Python sync and async drivers, then JS driver: each must cover create/output_text, raw streaming and available aggregate helper, image continuation, get/delete/store=false, manual output replay, reversed multiple outputs, status400/401/404/409/429/502/503, abort/timeout at three stream positions. Use real SDK exception types and close/abort APIs, then poll bounded cleanup and make a following successful request. Missing aggregate helper in a pinned SDK is reported explicitly, never claimed tested.
- [x] Pin Python SDK acceptance dependency to the verified installed version in tests/responses_compat/requirements.txt; resolve JS SDK once with `npm install --save-dev --save-exact openai` in its isolated directory and commit package/lock. Thereafter use npm ci. Add node_modules ignore scoped to tests/responses_compat. Do not modify WebUI dependencies.
- [x] Run `.venv/Scripts/python scripts/run_responses_compat.py --require-all`; expected all three client reports pass, no skipped drivers and every scenario accounted for. Also run full pytest (without requiring Node for ordinary unit suite), expected PASS. Commit `test: verify Responses compatibility with Python and JavaScript SDKs`.

### Task 6: CI, capabilities and operational documentation

**Files:** .github/workflows/ci.yml, docs/responses-api.md, docs/responses-api_en.md, README.md, README_en.md, AGENTS.md; SDK lock inputs from Task5.
**Interfaces:** one new responses-compat job sets Python3.12/Node20, installs project plus pinned SDK test inputs and npm ci in isolated folder, runs runner --require-all. Existing jobs and Python matrix remain.

- [x] Verify runner fails if Node or JS dependency is absent (Task5 harness), then add the required SDK job; expected workflow installs explicit dependencies and cannot silently skip JS. Check YAML with available parser and compare invocation to actual local runner; avoid installing a test framework solely to mirror YAML text.
- [x] Update bilingual examples for strict null/default normalization and effective tools, explicit parallel true with reversed result output loop, defaultfalse compatibility deviation, bounded-memory state, failed strict output and diagnostic fields. State that schema validation is after generation and parameter deltas may be emitted only after DSML parsing. Preserve unsupported capabilities and token-estimate caveats.
- [x] Run full pytest, smoke, scripts/run_e2e.py and runner --require-all; expected PASS, diff --check clean. Commit `ci: enforce Responses SDK compatibility and document tool semantics`.

### Task 7: Live acceptance and whole-branch review

**Files:** scripts/verify_responses_live.py and this plan's ledger; no HAR or credential fixture committed. Read tests' working SDK patterns from Task5.
**Interfaces:** live verifier accepts --har PATH and --client python|javascript|all, extracts authorized credentials only into TemporaryDirectory, launches normal app against real upstream and emits redacted summary. JS uses same temporary local endpoint through standard SDK; no OpenAI hosted call or extra multimodal parameters.

- [ ] Add bounded live verifier scenarios: text and prior code recall; image ACK without description then streamed hidden recall; add second image and compare; two-function request with explicit parallel true and complete reversed results; strict valid arguments and effective tool echo. Assertions separate transport/state correctness from model adherence; a refusal/failure remains visible, no fake completed or concealed retry. Temporaries clean up on failure.
- [ ] Run real Python and JS acceptance with E:/Projects/DS2API/har/chat.deepseek.com-chat-thinking.enable-search.enable.image-upload-new.har as already authorized. Expected both clients pass supported scenarios; if credentials expire, record exact safe failure class and complete all unaffected checks before asking for new credentials. Never print raw API exceptions that may embed request bodies/headers.
- [ ] Run final full pytest, smoke, e2e, runner --require-all and git diff --check, inspect every exit/status. Record versions, counts and live limitations in ledger. No repeated live run after green unless changes/failures justify it.
- [ ] Use requesting-code-review skill for one fresh independent read-only whole-branch review, scoped to approved spec/base887501e and all task commits. Fix substantive findings with RED/GREEN regressions, then full verification; log deferred minor findings with reasons. No implementer subagents unless user selects that method.
- [ ] Report implemented behavior, validation and limits; keep local branch reviewable. Do not infer approval to merge/push/release from design approval.

## Ledger and self-review

- User approved written spec on current turn. Native execution and final independent review match the previous phase; execution starts after user reviews this plan and confirms method.
- Pre-flight: Task1 error type/code flows into Tasks2/3/4; Task2 effective tools and pending IDs feed Task3; Task3 opt-in batch/on_token interface feeds Task4 and all SDK tests. No conflicting type or protocol dependency found; shared code never imports Responses types.
- Coverage: spec1 modules Tasks1–4; spec2 matrix Task5/6; spec3 strict Task2; spec4 batch/results Task2/3; spec5 errors/cancel Tasks1/3/5; spec6 diagnostics Task4; spec7 regression/live/review Tasks5–7. All five Review Focus items have owning test steps.
- Ruling at planning: keep one integrated plan because the three SDK drivers verify the same protocol/tool changes; they are not independent products. Persistent state is deliberately excluded.
- Ruling at planning: installed SDK ResponseError.code is a closed literal set; classify errors once but map the SSE projection to allowed codes and preserve safe original classification in optional error.type/param. Reusing arbitrary HTTP codes verbatim would break strict SDK parsing and violate the approved interoperability goal.
- Self-review: complete paths and producer/consumer signatures match; no unresolved placeholders. Supported-subset field-null check is an explicit official-doc verification step, not permission to silently broaden capability. No product code or dependencies changed during planning.
