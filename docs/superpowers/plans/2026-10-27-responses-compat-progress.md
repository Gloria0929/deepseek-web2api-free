# SDD ledger — plan: docs/superpowers/plans/2026-10-27-responses-compat.md

User approved the plan and inline execution on 2026-10-27. Branch feature/responses-compat; execution base 5cd5e8b; product base 887501e.

Pre-flight: Task1 safe error classification is consumed by Tasks2–4; effective tools/pending IDs from Task2 feed Task3; opt-in batch/on_token from Task3 feeds Task4 and SDK drivers. Shared modules must not import Responses. No interface conflicts found.

Ruling: installed SuperPower helper scripts/worktree skill are absent. Existing isolated feature branch is used; this tracked ledger replaces missing scratch helper bookkeeping, retaining task bases, rulings and evidence through compaction. No main changes or push.

Baseline: full pytest 232 passed in 5.84s. Task1 in progress.

Task1: RED 15 request/error cases failed as expected; implemented strict booleans, nullable defaults, parameter paths and classified SDK-safe errors. Corrected test expectations to the plan's SSE literal projection (401 invalid_prompt,503 server_error). Installed SDK parameter types independently confirm nullable metadata/include/background/parallel/truncation. Ruling: tools/tool_choice null are supported-subset convenience defaults despite SDK parameter annotations being non-null; effective normalized tools/choice are returned, no unsupported upstream behavior is implied.

Task1: complete. Full pytest247 passed; commit4722f08.

Task2: RED9 policy cases; normalized strict schemas, valid fallback/null/empty definitions, offline refs/finite JSON, 64-call policy and all-results-before-generation implemented. Updated old local-ref unit fixture to include the now-required call_id. Ruling: reject resource-scoping $id/anchors/dynamic refs explicitly, because root-only pointers would otherwise be misinterpreted and offline validation must not resolve other resources. Default schema annotations remain data, not subschemas.
