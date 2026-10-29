"""Fork feature tests: per-account statistics."""
from __future__ import annotations

from admin import StatsSnapshot


def test_per_account_stats():
    s = StatsSnapshot()
    s.record("deepseek-chat", 100, prompt_tokens=10, completion_tokens=20, account_id="acc-a")
    s.record("deepseek-chat", 200, prompt_tokens=5, completion_tokens=5, account_id="acc-a")
    s.record("deepseek-chat", 0, success=False, account_id="acc-b")
    s.record("deepseek-chat", 50)  # no account: global only
    assert s.total_requests == 4
    acc = s.account_stats()
    assert acc["acc-a"]["requests"] == 2
    assert acc["acc-a"]["success_requests"] == 2
    assert acc["acc-a"]["prompt_tokens"] == 15
    assert acc["acc-a"]["completion_tokens"] == 25
    assert acc["acc-a"]["avg_latency_ms"] == 150
    assert acc["acc-a"]["success_rate"] == 1.0
    assert acc["acc-b"]["requests"] == 1
    assert acc["acc-b"]["failed_requests"] == 1
    assert acc["acc-b"]["success_rate"] == 0.0
    assert len(acc) == 2  # the account-less record created no entry
