# SDD ledger — plan: docs/superpowers/plans/2026-10-27-responses-compat.md

User approved the plan and inline execution on 2026-10-27. Branch feature/responses-compat; execution base 5cd5e8b; product base 887501e.

Pre-flight: Task1 safe error classification is consumed by Tasks2–4; effective tools/pending IDs from Task2 feed Task3; opt-in batch/on_token from Task3 feeds Task4 and SDK drivers. Shared modules must not import Responses. No interface conflicts found.

Ruling: installed SuperPower helper scripts/worktree skill are absent. Existing isolated feature branch is used; this tracked ledger replaces missing scratch helper bookkeeping, retaining task bases, rulings and evidence through compaction. No main changes or push.

Baseline: full pytest 232 passed in 5.84s. Task1 in progress.

Task1: RED 15 request/error cases failed as expected; implemented strict booleans, nullable defaults, parameter paths and classified SDK-safe errors. Corrected test expectations to the plan's SSE literal projection (401 invalid_prompt,503 server_error). Installed SDK parameter types independently confirm nullable metadata/include/background/parallel/truncation. Ruling: tools/tool_choice null are supported-subset convenience defaults despite SDK parameter annotations being non-null; effective normalized tools/choice are returned, no unsupported upstream behavior is implied.

Task1: complete. Full pytest247 passed; commit4722f08.

Task2: RED9 policy cases; normalized strict schemas, valid fallback/null/empty definitions, offline refs/finite JSON, 64-call policy and all-results-before-generation implemented. Updated old local-ref unit fixture to include the now-required call_id. Ruling: reject resource-scoping $id/anchors/dynamic refs explicitly, because root-only pointers would otherwise be misinterpreted and offline validation must not resolve other resources. Default schema annotations remain data, not subschemas.

Task2: complete. Full pytest256 passed; commit21f68be.

Task3: RED4 batch/SDK cases; opt-in complete tool batches, cumulative validation before emit, explicit parallel true (defaultfalse), effective response flag, reversed output loop implemented. Removed obsolete parallel=true unsupported test; replaced with positive/negative batch tests. Fixed new test's missing json import after its intended request-level RED became GREEN. Existing cancellation/worker ownership remains shared; real TCP batch aborts belong to Task5.

Task3: complete. Full pytest259 passed, smoke passed; commitf51ed8d.

Task4: complete. RED4 generic observation cases plus route integration RED; full pytest264 passed. Added generic optional timing callbacks and request-local whitelisted logging; first-upstream and first-visible markers, finish-once and cancellation cleanup callbacks. Logger failures do not affect business results; synthetic input/output/URL sentinels absent. No database/UI/global diagnostic buffer.

Task4 commit9ded0d8.

Task5: RED2 HTTP harness/owned process cases, then GREEN. Real uvicorn loopback app retains actual pool/semaphore/state/parser and swaps only upstream adapters. Diagnosed inherited SOCKS proxy requirement: loopback SDK clients explicitly trust_env=false. Graceful authenticated test-only shutdown closes Windows log handles. SDK driver scenario selection initially mistook historical batch prompt for a fresh tool request; fixed test backend's last-message discrimination, not product behavior.

Task5 SDK evidence: Python3.39.0 sync + async and JS7.45.0, each10 required groups PASS over actual HTTP. Groups include raw and aggregate streams, image append/replay, tool-result images/manual Items/reverse calls, seven HTTP error statuses, invalid second/later batches, abort before upstream/text/tool and timeouts before headers/text/tool, followed by successful recovery. Shared manifest enforced, missing driver or Node is failure. JS dependency isolated and locked. No live credentials in fixtures or CI.

Task5: complete. Full pytest266 passed; commit9bedffd. Task6 adds a required three-SDK CI job and bilingual strict/parallel/default/error/diagnostic docs. Explicit referencing>=0.28.4 matches installed jsonschema's declared minimum for direct Registry import. No available YAML parser installed; reviewed workflow structure and run the exact local commands rather than add a parser-only dependency.
