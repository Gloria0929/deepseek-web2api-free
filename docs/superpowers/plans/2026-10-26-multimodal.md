# Multimodal implementation plan

**Goal:** Accept image input on OpenAI chat completions and Anthropic messages, in streaming and nonstreaming modes.

**Architecture:** Protocol edges normalize image blocks into ordered text markers plus validated image assets. A shared turn preparer uploads assets through the selected account's file client and supplies completion references; output formatting remains unchanged.

**Spec:** [Repository design and verification record](../../multimodal.md). Original private HAR recordings and credential-bearing artifacts are intentionally excluded.

**Execution:** Inline, as requested by the user. After implementation, the user explicitly authorized documentation alignment, commit and push on 2026-10-26; no release/tag requested.

## Constraints and decisions

- Preserve text-only and tool-call behavior. Support JPEG/PNG/WebP; reject unsupported input explicitly.
- Default limits: 8 images/request, 10 MiB/image, 32 MiB decoded total, 40 MiB JSON request, 25 million pixels/image. Environment overrides documented.
- Fetch HTTP(S) images through a separate unauthenticated, DNS-pinned connection; validate every redirect and reject non-global addresses. No ambient proxy credentials.
- Upload through existing account transport and fresh upload PoW; poll repeated file_ids parameters until SUCCESS, with a 60 second processing deadline.
- Cache only file references/metadata in existing caller-scoped, account-bound session state; no image persistence or shared global file cache.
- Stream setup (upload/poll) completes before response headers. Blocking preparation runs off the event loop.
- Image errors use protocol-appropriate envelopes and never automatically poison account state.
- Retries retain image references and replay full available request input when a new upstream session is created.

## Review focus

1. Same text with changed/reordered images must not compare as identical history.
2. Tool-result nested Anthropic images preserve their tool association.
3. Remote URL redirects, alternate IP forms, DNS rebinding, image decompression and body limits.
4. Upload failures/cancellation release acquired accounts and do not commit history.
5. Account switches and new-session retries must not cross credentials or lose references.

## Tasks

- [x] 1. Add failing tests for image normalization, validation and secure remote resolution; implement image_inputs.py and image_sources.py with Pillow validation.
- [x] 2. Add failing tests for multipart upload/status/error/timeout handling; implement deepseek_files.py, composed by DeepSeekAdapter.
- [x] 3. Add failing tests for shared turn preparation, image-aware history and retry references; implement turn_preparation.py and adapter/session hooks.
- [x] 4. Add two-protocol × two-mode endpoint tests, nested tool-result and failure/release tests; wire preprocessing and shared preparation into server/anthropic models.
- [x] 5. Run complete pytest, smoke and e2e checks; real-image downstream acceptance using the new HAR; update bilingual usage docs and AGENTS capability notes.
- [x] 6. Request a fresh read-only review, address material findings with regression tests, and report final verification and limitations.

## Execution ledger

Initial repository: main at 97c21e8; clean working tree. Existing real probe proved repeated query parameters and same-account cross-session reuse. Defaults above are local resource policy, not claims of upstream limits.

Final ledger: 195 tests pass; smoke 7 groups and all 3 e2e scenarios pass. Seven full live downstream scenarios pass, followed by final OpenAI/Anthropic thinking-stream checks. Public URL requests are rejected in this environment because DNS maps the host to 198.18.0.180; no successful real public-URL download is claimed. Reviewer findings on latest-only retry refs, acquisition cancellation and early stream cleanup were reproduced with failing tests and fixed; final review reports no unresolved material findings.

Ruling: add image_http.py for HTTP envelopes/body bounds so image domain modules remain independent of FastAPI. Use bounded daemon DNS workers (4 slots) to enforce caller deadlines without unbounded resolver work. Raw asyncio cancellation must drain active stream reads before closing/returning the account. CI now discovers all tests rather than a fixed list. Documentation and publication were subsequently authorized by the user. Verification records below describe the development checkpoint; publication is recorded in Git history.
