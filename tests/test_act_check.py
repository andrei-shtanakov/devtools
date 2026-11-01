"""Полная проверка доставки на T (спека §6, §6.4, §8, §10)."""

from __future__ import annotations

import pytest

pytest.importorskip("steward")

from governance.act_check import (  # noqa: E402
    ADMISSION,
    EPOCH_GAP,
    POLICY_CHANGED,
    REQUEST_BINDING,
    LegacyAccepted,
    _requires,
    check_delivery,
    live_policy_refusal,
)
from governance.act_decision import Accepted, Rejected, WaitingDelivery  # noqa: E402
from governance.act_epochs import Epoch  # noqa: E402
from governance.act_request import PolicyPin  # noqa: E402
from governance.act_time import Ambiguous, Unavailable, parse_ts  # noqa: E402
from tests.act_cases import code  # noqa: E402
from tests.act_cases_facts import PATH, act, context, review, sha, ts  # noqa: E402

TWO_EPOCHS_GAP = (
    Epoch(1, sha("p1"), sha("p1"), ts("09:00:00"), "нарушение"),
    Epoch(2, sha("p2")),
)
ANCHORS = {sha("p1"): parse_ts(ts("08:00:00")), sha("p2"): parse_ts(ts("11:00:00"))}


def test_happy_path() -> None:
    """§8.1 №1 / приёмка №1."""
    assert isinstance(check_delivery(context()), Accepted)


def test_policy_changed_before_merge() -> None:
    """§8.1 №4, приёмка №8, №14."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:30:00"))}
    assert code(check_delivery(context(ranges=ranges))) == POLICY_CHANGED


def test_policy_changed_after_merge_accepted() -> None:
    """§8.1 №5, приёмка №15."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "10:30:00"))}
    assert isinstance(check_delivery(context(ranges=ranges)), Accepted)


def test_revert_to_same_bytes_is_new_activation() -> None:
    """§8.1 №12, приёмка №22."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:55:00"), act("p3", "09:56:00"))}
    assert code(check_delivery(context(ranges=ranges))) == POLICY_CHANGED


def test_two_activations_same_second_first_parent_wins() -> None:
    """Приёмка №30."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:00:00"), act("p3", "09:00:00"))}
    assert code(check_delivery(context(ranges=ranges, pin="p2"))) == POLICY_CHANGED
    assert isinstance(check_delivery(context(ranges=ranges, pin="p3")), Accepted)


def test_activation_same_second_differing_outcome_ambiguous() -> None:
    """§8.1 №10, приёмка №20."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "10:00:00"))}
    assert check_delivery(context(ranges=ranges)) == Ambiguous()


def test_mode_switch_same_second_ambiguous() -> None:
    """Приёмка №27."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "10:00:00"))}
    modes = {sha("p1"): "merge", sha("p2"): "review"}
    ctx = context(
        ranges=ranges,
        modes=modes,
        request_act=None,
        legacy=LegacyAccepted("df-reviewer[bot]"),
    )
    assert check_delivery(ctx) == Ambiguous()


def test_first_merge_entry_same_second_admitted() -> None:
    """Приёмка №27a."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "10:00:00"))}
    modes = {sha("p1"): None, sha("p2"): "merge"}
    ctx = context(
        ranges=ranges,
        modes=modes,
        request_act=None,
        legacy=LegacyAccepted("df-reviewer[bot]"),
    )
    assert check_delivery(ctx) == LegacyAccepted("df-reviewer[bot]")


@pytest.mark.parametrize("legacy_code", ["legacy_merger", "legacy_unpublished"])
def test_switch_in_t_rejected_both_sides_is_rejection(legacy_code: str) -> None:
    """P4-1: merge→review в T, немаркированная заявка, старый код отказал (мержер или
    неполный состав) — отказ в обоих порядках → отказ, не неоднозначность."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "10:00:00"))}
    modes = {sha("p1"): "merge", sha("p2"): "review"}
    ctx = context(
        ranges=ranges, modes=modes, request_act=None, legacy=Rejected(legacy_code)
    )
    assert code(check_delivery(ctx)) in (legacy_code, ADMISSION)


def test_rejected_for_different_reasons_is_one_verdict() -> None:
    """Approve в T + запрещённый мержер: D1 в одном порядке, D3 в другом — отказ, не
    неоднозначность (§8.2: сравниваются исходы)."""
    ctx = context(approve_at="10:00:00", merger=("stranger", "User"))
    assert code(check_delivery(ctx)) in ("d1_no_act", "d3_merger")


def test_policy_changed_constant_matches_ledger() -> None:
    from governance.approval_facts import INVALIDATION_POLICY_CHANGED

    assert POLICY_CHANGED == INVALIDATION_POLICY_CHANGED


def test_only_approve_in_merge_second_ambiguous() -> None:
    """Приёмка №47."""
    assert check_delivery(context(approve_at="10:00:00")) == Ambiguous()


def test_dismissal_in_merge_second_ambiguous() -> None:
    """Приёмка №46 — approve раньше T, отзыв в T."""
    assert check_delivery(context(dismissals=(("11", "10:00:00"),))) == Ambiguous()


def test_interepoch_gap_refused_for_review() -> None:
    """Приёмка №42 / e3."""
    ctx = context(epochs=TWO_EPOCHS_GAP, anchor_at=ANCHORS)
    assert code(check_delivery(ctx)) == EPOCH_GAP


def test_interepoch_gap_refused_for_unmarked_merge_mode() -> None:
    """e3 для старого читателя: OLD_READER в разрыве не выдаётся."""
    modes = {sha("p1"): "merge"}
    ctx = context(
        epochs=TWO_EPOCHS_GAP, anchor_at=ANCHORS, modes=modes, request_act=None
    )
    assert code(check_delivery(ctx)) == EPOCH_GAP


def test_closed_epoch_legit_old_act() -> None:
    """Приёмка №41 / e2."""
    epochs = (
        Epoch(1, sha("p1"), sha("p1"), ts("12:00:00"), "позже"),
        Epoch(2, sha("p2")),
    )
    anchors = {sha("p1"): parse_ts(ts("08:00:00")), sha("p2"): parse_ts(ts("13:00:00"))}
    assert isinstance(
        check_delivery(context(epochs=epochs, anchor_at=anchors)), Accepted
    )


def test_new_epoch_does_not_need_old_range() -> None:
    """e7: акт в E2, диапазон E1 недоступен — проверка от якоря E2."""
    epochs = (
        Epoch(1, sha("p0"), sha("p0"), ts("07:00:00"), "разрыв"),
        Epoch(2, sha("p1")),
    )
    anchors = {sha("p0"): parse_ts(ts("06:00:00")), sha("p1"): parse_ts(ts("08:00:00"))}
    ranges = {1: Unavailable("объекты удалены"), 2: (act("p1", "08:00:00"),)}
    ctx = context(epochs=epochs, anchor_at=anchors, ranges=ranges)
    assert isinstance(check_delivery(ctx), Accepted)


def test_new_epoch_cannot_restart_migration() -> None:
    """P2-1: E1 переключил продукт на review; E2 начинается с merge — обратное
    переключение через границу эпох → unavailable, не OLD_READER."""
    epochs = (
        Epoch(1, sha("p0"), sha("p0"), ts("07:00:00"), "разрыв"),
        Epoch(2, sha("p1")),
    )
    anchors = {sha("p0"): parse_ts(ts("06:00:00")), sha("p1"): parse_ts(ts("08:00:00"))}
    ranges = {1: (act("p0", "06:00:00"),), 2: (act("p1", "08:00:00"),)}
    modes = {sha("p0"): "review", sha("p1"): "merge"}
    ctx = context(
        epochs=epochs,
        anchor_at=anchors,
        ranges=ranges,
        modes=modes,
        request_act=None,
        pin="p1",
    )
    assert isinstance(check_delivery(ctx), Unavailable)


def test_new_epoch_without_entry_after_review_refused() -> None:
    """P2-1: E2 без записи продукта после review в E1 → отказ admission."""
    epochs = (
        Epoch(1, sha("p0"), sha("p0"), ts("07:00:00"), "разрыв"),
        Epoch(2, sha("p1")),
    )
    anchors = {sha("p0"): parse_ts(ts("06:00:00")), sha("p1"): parse_ts(ts("08:00:00"))}
    ranges = {1: (act("p0", "06:00:00"),), 2: (act("p1", "08:00:00"),)}
    modes = {sha("p0"): "review", sha("p1"): None}
    ctx = context(
        epochs=epochs,
        anchor_at=anchors,
        ranges=ranges,
        modes=modes,
        request_act=None,
        pin="p1",
    )
    assert code(check_delivery(ctx)) == ADMISSION


def test_merge_mode_in_new_epoch_needs_prior_history() -> None:
    """Без предыстории режим merge в E2 не проверяем → unavailable."""
    epochs = (
        Epoch(1, sha("p0"), sha("p0"), ts("07:00:00"), "разрыв"),
        Epoch(2, sha("p1")),
    )
    anchors = {sha("p0"): parse_ts(ts("06:00:00")), sha("p1"): parse_ts(ts("08:00:00"))}
    ranges = {1: Unavailable("объекты удалены"), 2: (act("p1", "08:00:00"),)}
    ctx = context(
        epochs=epochs,
        anchor_at=anchors,
        ranges=ranges,
        modes={sha("p1"): "merge"},
        request_act=None,
        pin="p1",
    )
    assert isinstance(check_delivery(ctx), Unavailable)


def test_old_anchor_time_not_needed_for_new_epoch() -> None:
    """P1-4 / e7: время якоря E1 недоступно — акт E2 всё равно проверяется."""
    epochs = (
        Epoch(1, sha("p0"), sha("p0"), ts("07:00:00"), "разрыв"),
        Epoch(2, sha("p1")),
    )
    anchors = {
        sha("p0"): Unavailable("нет объекта"),
        sha("p1"): parse_ts(ts("08:00:00")),
    }
    ranges = {1: Unavailable("объекты удалены"), 2: (act("p1", "08:00:00"),)}
    ctx = context(epochs=epochs, anchor_at=anchors, ranges=ranges, pin="p1")
    assert isinstance(check_delivery(ctx), Accepted)


def test_unreadable_policy_after_t_does_not_block_act() -> None:
    """P2-2: набор активации ПОСЛЕ T не читается — законный акт принят."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "11:00:00"))}
    ctx = context(ranges=ranges, unreadable_sets=frozenset({sha("p2")}))
    assert isinstance(check_delivery(ctx), Accepted)


def test_own_epoch_range_unavailable() -> None:
    """e5 без доступных объектов своей эпохи → unavailable, не отказ."""
    ctx = context(ranges={1: Unavailable("объекты удалены")})
    assert isinstance(check_delivery(ctx), Unavailable)


def test_unreadable_policy_set_is_unavailable() -> None:
    ctx = context(unreadable_sets=frozenset({sha("p1")}))
    assert isinstance(check_delivery(ctx), Unavailable)


def test_unmarked_after_review_mode_refused() -> None:
    """Приёмка №24."""
    assert code(check_delivery(context(request_act=None))) == ADMISSION


def test_anchor_and_activation_are_one_event() -> None:
    """Якорь эпохи в секунду доставки — одно событие, а не два независимых."""
    epochs = (Epoch(1, sha("p0"), sha("p0"), ts("10:00:00"), "x"), Epoch(2, sha("p1")))
    anchors = {sha("p0"): parse_ts(ts("07:00:00")), sha("p1"): parse_ts(ts("10:00:00"))}
    ranges = {1: (act("p0", "07:00:00"),), 2: (act("p1", "10:00:00"),)}
    ctx = context(epochs=epochs, anchor_at=anchors, ranges=ranges, pin="p1")
    assert check_delivery(ctx) == Ambiguous()


def test_new_head_after_approve_is_no_act() -> None:
    """Приёмка №2: approve на прежний коммит."""
    ctx = context(approve_commit="2" * 40)
    assert code(check_delivery(ctx)) == "d1_no_act"


def test_dismissed_before_merge_rejected() -> None:
    """§8.1 №2 / приёмка №3."""
    ctx = context(dismissals=(("11", "09:55:00"),))
    assert code(check_delivery(ctx)) == "d1_no_act"


def test_dismissed_after_merge_accepted() -> None:
    """§8.1 №3: отзыв после T на срезе отсутствует (previous_state)."""
    ctx = context(dismissals=(("11", "10:30:00"),))
    assert isinstance(check_delivery(ctx), Accepted)


def test_approver_outside_list_rejected() -> None:
    """Приёмка №4."""
    ctx = context(reviews=(review("11", "mallory", "09:50:00"),))
    assert code(check_delivery(ctx)) == "d1_no_act"


def test_not_merged_waits() -> None:
    """Приёмка №5."""
    assert check_delivery(context(merged=False)) == WaitingDelivery()


def test_agent_outside_merger_list() -> None:
    """Приёмка №6 и №16: агент (или machine user) вне AUTHORIZED_MERGER_ACCOUNTS."""
    assert code(check_delivery(context(merger=("ai-prosto", "User")))) == "d3_merger"
    assert code(check_delivery(context(merger=("ai-prosto", "Bot")))) == "d3_merger"


def test_old_reader_without_legacy_verdict_unavailable() -> None:
    """b4: OLD_READER без вычисленного вердикта старого кода — `Unavailable`, не отказ
    и не «принято» (`_legacy_verdict`)."""
    got = check_delivery(context(merged_at="07:00:00", request_act=None, legacy=None))
    assert (
        isinstance(got, Unavailable)
        and got.reason == "вердикт старой проверки не вычислен"
    )


def test_activations_out_of_order_unavailable() -> None:
    """b4: `merged_at` активаций против first-parent — диапазон не принимается (`_span`)."""
    ranges = {1: (act("p1", "09:00:00"), act("p2", "08:30:00"))}
    got = check_delivery(context(ranges=ranges))
    assert isinstance(got, Unavailable)
    assert got.reason == "merged_at активаций противоречит first-parent"


def test_before_bootstrap_unmarked_goes_to_old_reader() -> None:
    """Приёмка №7: доставка до якоря эпохи 1, заявка без маркера — старый код."""
    ctx = context(merged_at="07:00:00", request_act=None, legacy=LegacyAccepted("bob"))
    assert check_delivery(ctx) == LegacyAccepted("bob")


def test_before_bootstrap_marked_rejected() -> None:
    ctx = context(merged_at="07:00:00")
    assert code(check_delivery(ctx)) == "marker_before_mode"


def test_actors_only_change_is_policy_changed() -> None:
    """Приёмка №9: версия — активация, а не файл утверждающих."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:00:00"))}
    assert code(check_delivery(context(ranges=ranges))) == POLICY_CHANGED


def test_bytes_and_content_are_distinct_refusals() -> None:
    """Приёмка №18 (D2): I-bytes и I-content — разные исходы."""
    moved = context(blobs={PATH: ("c" * 40, "b" * 40)})
    assert code(check_delivery(moved)) == "i_bytes"
    deleted = context(blobs={PATH: (None, "b" * 40)})  # О1: удалён в merge-коммите
    assert code(check_delivery(deleted)) == "i_bytes"
    assert code(check_delivery(context(self_hash="z" * 40))) == "i_content"


@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        ({"p1": "review", "p2": None}, ADMISSION),  # запись удалена → "" (п. 4, №26)
        ({"p1": "review", "p2": "merge"}, Unavailable),  # обратное переключение
    ],
)
def test_mode_entry_removed_or_reverted(modes: dict, expected: object) -> None:
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:00:00"))}
    ctx = context(ranges=ranges, modes={sha(k): v for k, v in modes.items()}, pin="p2")
    got = check_delivery(ctx)
    if expected is Unavailable:
        assert isinstance(got, Unavailable)
    else:
        assert code(got) == expected


def test_mixed_policy_refused() -> None:
    """Приёмка №32: files_sha256 пина не от набора активации."""
    ctx = context(pin_digests={"policy/approvers.env": "0" * 64})
    assert code(check_delivery(ctx)) == "mixed_policy"


def test_other_product_switch_does_not_touch_merge_product() -> None:
    """Приёмка №38: режим считается по продукту доставки."""
    ctx = context(
        modes={sha("p1"): "merge"}, request_act=None, legacy=LegacyAccepted("bob")
    )
    assert check_delivery(ctx) == LegacyAccepted("bob")


def test_live_pin_against_current_activation() -> None:
    """Приёмка №44 (живое правило; вызывает Task 13/15)."""
    pin = PolicyPin("owner/approval-policy", "main", sha("p1"), {})
    assert live_policy_refusal(pin, sha("p1")) is None
    assert live_policy_refusal(pin, sha("p2")).startswith(POLICY_CHANGED)


def test_unrelated_dismissal_in_merge_second_accepted() -> None:
    """Приёмка №46a: отзыв ревью неавторизованного логина в секунду T."""
    reviews = (
        review("11", "alice", "09:50:00"),
        review("12", "mallory", "09:40:00", state="DISMISSED"),
    )
    ctx = context(reviews=reviews, dismissals=(("12", "10:00:00"),))
    assert isinstance(check_delivery(ctx), Accepted)


def test_pending_review_ignored() -> None:
    """Приёмка №48."""
    reviews = (
        review("11", "alice", "09:50:00"),
        review("12", "alice", None, state="PENDING"),
    )
    assert isinstance(check_delivery(context(reviews=reviews)), Accepted)


def test_both_approvals_dismissed_at_t_ambiguous() -> None:
    """Приёмка №49: среди порядков есть «оба отзыва до T» — акта нет."""
    reviews = (
        review("11", "alice", "09:50:00", state="DISMISSED"),
        review("12", "bob", "09:51:00", state="DISMISSED"),
    )
    ctx = context(
        reviews=reviews,
        approvers=("alice", "bob"),
        dismissals=(("11", "10:00:00"), ("12", "10:00:00")),
    )
    assert check_delivery(ctx) == Ambiguous()


def test_manifest_product_must_be_delivery_repo() -> None:
    """Заметки части A, п. 7: манифест чужого продукта не судится чужой политикой."""
    ctx = context(request_product="owner/other")
    assert code(check_delivery(ctx)) == REQUEST_BINDING


def test_requires_orders_review_and_same_second_activations() -> None:
    """Заметки части A, п. 3: отправка → отзыв; активации одной секунды — first-parent."""
    ranges = {1: (act("p1", "08:00:00"), act("p2", "09:00:00"), act("p3", "09:00:00"))}
    ctx = context(ranges=ranges, dismissals=(("11", "09:55:00"),))
    req = _requires(ctx)
    assert req[("dismissal", "11")] == frozenset({("review", "11")})
    assert req[("activation", sha("p3"))] == frozenset({("activation", sha("p2"))})
    assert ("activation", sha("p2")) not in req
