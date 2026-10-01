"""Журнал вызовов установки и запрет по лимиту (спека среза 1, §5.5)."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conductor.app_calls import AppCalls, RateInfo, init_host, journal_path

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)


def _calls(tmp_path: Path) -> AppCalls:
    init_host(tmp_path, 1, 2, T0)
    calls, finding = AppCalls.open(tmp_path, 1, 2, T0)
    assert calls is not None and finding is None
    return calls


def _call(calls: AppCalls, klass: str, at: datetime, outcome: str, rate=RateInfo()):
    seq = calls.begin(klass, at)
    calls.end(seq, at, outcome, 200 if outcome == "ok" else 403, rate, klass)


def test_empty_journal_has_no_block(tmp_path: Path) -> None:
    assert _calls(tmp_path).blocked_until() is None


def test_retry_after_is_kept_fully_beyond_65_min(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    _call(calls, "create", T0, "rate_limited", RateInfo(retry_after_s=7200))
    assert calls.blocked_until() == T0 + 2 * H


def test_primary_reset_and_ok_with_zero_remaining(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    reset = int((T0 + 30 * timedelta(minutes=1)).timestamp())
    _call(calls, "update", T0, "ok", RateInfo(remaining=0, reset=reset))
    assert calls.blocked_until() == datetime.fromtimestamp(reset, UTC)


def test_unknown_term_escalates_per_class_to_8h(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    expected = [1, 2, 4, 8, 8]
    for i, hours in enumerate(expected):
        at = T0 + timedelta(days=i)
        _call(calls, "create", at, "rate_limited")
        assert calls.blocked_until() == at + hours * H


def test_service_success_does_not_reset_create(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    _call(calls, "create", T0, "rate_limited")
    _call(calls, "service", T0 + 2 * H, "ok")
    _call(calls, "create", T0 + 3 * H, "rate_limited")
    assert calls.blocked_until() == T0 + 3 * H + 2 * H


def test_same_class_success_resets(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    _call(calls, "create", T0, "rate_limited")
    _call(calls, "create", T0 + 2 * H, "ok")
    _call(calls, "create", T0 + 3 * H, "rate_limited")
    assert calls.blocked_until() == T0 + 4 * H


def test_begin_without_end_blocks_one_hour(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    calls.begin("create", T0)
    reopened, _ = AppCalls.open(tmp_path, 1, 2, T0)
    assert reopened is not None and reopened.blocked_until() == T0 + H


def test_truncated_tail_becomes_orphan_and_file_stays_appendable(
    tmp_path: Path,
) -> None:
    calls = _calls(tmp_path)
    _call(calls, "service", T0, "ok")
    path = journal_path(tmp_path, 1, 2)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"t": "be')
    stamp = (T0 + H).timestamp()
    os.utime(path, (stamp, stamp))  # оборванная строка «случилась» в T0 + 1 ч
    reopened, finding = AppCalls.open(tmp_path, 1, 2, T0)
    assert reopened is not None and finding is None
    assert reopened.blocked_until() == T0 + 2 * H
    _call(reopened, "service", T0 + 3 * H, "ok")
    assert AppCalls.open(tmp_path, 1, 2, T0)[0] is not None


def test_lost_journal_blocks_with_finding(tmp_path: Path) -> None:
    _calls(tmp_path)
    journal_path(tmp_path, 1, 2).unlink()
    calls, finding = AppCalls.open(tmp_path, 1, 2, T0)
    assert finding == "RATE-STATE-LOST" and calls is not None
    assert calls.blocked_until() == T0 + H


def test_corrupt_middle_line_is_moved_and_blocks(tmp_path: Path) -> None:
    _calls(tmp_path)
    journal_path(tmp_path, 1, 2).write_text('x\n{"t": "lost"}\n', encoding="utf-8")
    calls, finding = AppCalls.open(tmp_path, 1, 2, T0)
    assert finding == "RATE-STATE-LOST" and calls is not None
    assert (tmp_path / "corrupt").is_dir()


def test_without_host_init_no_calls(tmp_path: Path) -> None:
    calls, finding = AppCalls.open(tmp_path, 1, 2, T0)
    assert calls is None and finding == "OPSTATE-UNINITIALIZED"


def test_block_is_shared_by_profiles(tmp_path: Path) -> None:
    fleet = _calls(tmp_path)
    _call(fleet, "create", T0, "rate_limited", RateInfo(retry_after_s=600))
    acceptance, _ = AppCalls.open(tmp_path, 1, 2, T0)
    assert acceptance is not None
    assert acceptance.blocked_until() == T0 + timedelta(seconds=600)


def test_init_host_after_lost_journal_keeps_ban(tmp_path: Path) -> None:
    """Регрессия P2-5: HOST_INIT есть, журнала нет → событие lost, не чистый лист."""
    init_host(tmp_path, 1, 2, T0)
    journal_path(tmp_path, 1, 2).unlink()
    init_host(tmp_path, 1, 2, T0 + H)  # init-state другого профиля
    calls, finding = AppCalls.open(tmp_path, 1, 2, T0 + H)
    assert calls is not None and finding is None
    assert calls.blocked_until() == T0 + 2 * H
    assert [r["t"] for r in calls.rows] == ["lost"]


def test_first_init_host_is_clean(tmp_path: Path) -> None:
    init_host(tmp_path, 1, 2, T0)
    calls, _ = AppCalls.open(tmp_path, 1, 2, T0)
    assert calls is not None and calls.rows == [] and calls.blocked_until() is None


def test_truncated_tail_repair_failure_is_rate_state_lost(
    tmp_path: Path, monkeypatch
) -> None:
    """Ревью #538: починка оборванного хвоста не записалась (диск, read-only) —
    деградация RATE-STATE-LOST, как при утрате журнала, а не исключение."""
    import conductor.app_calls as app_calls

    calls = _calls(tmp_path)
    calls.begin("create", T0)
    with open(journal_path(tmp_path, 1, 2), "a", encoding="utf-8") as fh:
        fh.write('{"t": "be')

    def boom(*_: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(app_calls, "write_atomic", boom)
    assert AppCalls.open(tmp_path, 1, 2, T0) == (None, "RATE-STATE-LOST")
