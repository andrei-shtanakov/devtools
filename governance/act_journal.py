"""Журнал событий периметра для потребителя (спека A rev 16 §6.3, спека B §6.1).

Хранилище и автомат — этап 2a установщика (`df.publisher.perimeter`). В
установке `df` (есть обёртка издателя) потребитель спрашивает издателя
(`df.perimeter_client`); без неё — режим разработки: то же хранилище в
`$XDG_STATE_HOME/darkfactory/perimeter/root/…`, без гарантии защиты от агента
(спека §6.3; решение плана Р-5). Модуль НЕ входит в цепочку `act_premerge`.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from df import perimeter_client
from df.paths import Layout, default_layout
from df.publisher import perimeter as store
from df.publisher import request as preq
from df.stage2_config import TrustBinding
from governance.act_time import Unavailable
from governance.act_trust import STALE, TrustSnapshot

DEV_ROOT = Path("darkfactory") / "perimeter" / "root"
Reread = Callable[[], TrustSnapshot | Unavailable]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _publisher_present() -> bool:
    return (default_layout().libexec / "df-publish").exists()


class ForgeEpochs:
    """`EpochSource` издателя поверх чтения снимка `df-trust` с форджа."""

    def __init__(self, reread: Reread) -> None:
        self._reread = reread

    def head(self, root: tuple[str, int, str]) -> store.Snapshot | store.Unavailable:
        snap = self._reread()
        if isinstance(snap, Unavailable):
            return store.Unavailable(f"df-trust: {snap.reason}")
        if snap.root.id_tuple != tuple(root):
            return store.Unavailable("root_mismatch")
        return store.Snapshot(snap.root.snapshot_sha, snap.epochs[-1].epoch)


def _root_id(snap: TrustSnapshot) -> dict[str, object]:
    host, repository_id, branch = snap.root.id_tuple
    return {"host": host, "repository_id": repository_id, "branch": branch}


def _status_of(resp: preq.Response, snap: TrustSnapshot) -> str | Unavailable:
    if resp.outcome == "stale_snapshot":
        return STALE
    result = resp.result
    state = result.get("state")
    if state not in ("blocked", "clear"):
        return Unavailable(f"периметр: {result.get('reason') or resp.reason}")
    if (result.get("snapshot_sha"), result.get("last_epoch")) != (
        snap.root.snapshot_sha,
        snap.epochs[-1].epoch,
    ):
        return Unavailable("периметр: ответ не о снимке решения")
    return str(state)


class DevJournal:
    """Режим разработки: хранилище этапа 2a в каталоге пользователя."""

    def __init__(
        self, state_home: Path, run_id: str, reread: Reread, policy_repository_id: int
    ) -> None:
        base = (state_home / DEV_ROOT).resolve()
        base.mkdir(parents=True, exist_ok=True)
        self.layout = Layout(base)
        self.run_id = run_id
        self.epochs = ForgeEpochs(reread)
        # настоящий id approval-policy из привязки: по нему издатель пишет
        # `violated_repository_id` события роли approval-policy (B1-18)
        self.policy_repository_id = policy_repository_id

    def _open(self) -> store.Db | Unavailable:
        try:
            self.layout.perimeter_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            store.create_db(
                self.layout
            )  # created | present; lost/integrity — исключение
            return store.open_db(self.layout)
        except (store.Unavailable, OSError) as exc:
            return Unavailable(f"журнал периметра (dev): {getattr(exc, 'reason', exc)}")

    def _request(self, op: str, fields: Mapping[str, object]) -> preq.Request:
        data = {
            "schema": preq.REQUEST_SCHEMA,
            "request_id": f"perimeter-{uuid.uuid4().hex}",
            "run_id": self.run_id,
            **fields,
        }
        return preq.parse(op, json.dumps(data, sort_keys=True).encode("utf-8"))

    def status(self, snap: TrustSnapshot) -> str | Unavailable:
        req = self._request(
            "perimeter-status",
            {
                "root_id": _root_id(snap),
                "expected_snapshot_sha": snap.root.snapshot_sha,
            },
        )
        db = self._open()
        if isinstance(db, Unavailable):
            return db
        with db:
            try:
                store.admit(db, self.epochs, snap.root.id_tuple)
            except store.Unavailable as exc:
                return Unavailable(f"журнал периметра (dev): {exc.reason}")
            resp = store.perimeter_status(db.conn, req, self.epochs)
        return _status_of(resp, snap)

    def record(
        self, snap: TrustSnapshot, source: str, detail: str
    ) -> Unavailable | None:
        req = self._request(
            "record-violation",
            {
                "root_id": _root_id(snap),
                "source": source,
                "observed_at": _now(),
                "detail": detail[: preq.MAX_DETAIL],
            },
        )
        host, repository_id, branch = snap.root.id_tuple
        binding = TrustBinding(
            host,
            repository_id,
            snap.full_name,
            branch,
            self.policy_repository_id,
            "public",
        )
        db = self._open()
        if isinstance(db, Unavailable):
            return db
        with db:
            try:
                store.admit(db, self.epochs, snap.root.id_tuple)
                store.record_violation(
                    db.conn,
                    os.getuid(),
                    req,
                    f"dev:{req.request_id}",
                    self.epochs,
                    binding,
                )
            except store.Unavailable as exc:
                return Unavailable(f"журнал периметра (dev): {exc.reason}")
        return None


class InstalledJournal:
    """Установка `df`: операции издателя `perimeter-status` / `record-violation`."""

    def __init__(self, df_run_id: str, layout: Layout | None = None) -> None:
        self.run_id = df_run_id
        self.layout = layout

    def status(self, snap: TrustSnapshot) -> str | Unavailable:
        moved: list[bool] = []

        def reload() -> perimeter_client.TrustView:
            moved.append(True)
            raise RuntimeError("снимок df-trust сдвинулся — перечитать доверие")

        got = perimeter_client.status(
            self.run_id,
            perimeter_client.RootId(*snap.root.id_tuple),
            perimeter_client.TrustView(snap.root.snapshot_sha, snap.epochs[-1].epoch),
            reload=reload,
            layout=self.layout,
        )
        if moved:
            return STALE
        if got.state == "unavailable":
            return Unavailable(f"периметр: {got.reason}")
        return got.state

    def record(
        self, snap: TrustSnapshot, source: str, detail: str
    ) -> Unavailable | None:
        data = {
            "schema": preq.REQUEST_SCHEMA,
            "request_id": f"perimeter-{uuid.uuid4().hex}",
            "run_id": self.run_id,
            "root_id": _root_id(snap),
            "source": source,
            "observed_at": _now(),
            "detail": detail[: preq.MAX_DETAIL],
        }
        try:
            raw = perimeter_client.sudo_call(
                json.dumps(data, sort_keys=True).encode("utf-8"),
                self.layout,
                operation="record-violation",
            )
            resp = preq.parse_response(raw.decode("utf-8").strip())
        except (OSError, preq.BadResponse) as exc:
            return Unavailable(f"record-violation: {exc}")
        if resp.outcome != "done":
            return Unavailable(f"record-violation: {resp.outcome}: {resp.reason}")
        return None


def journal_for(
    *,
    state_home: Path,
    run_id: str,
    reread: Reread,
    env: Mapping[str, str],
    policy_repository_id: int,
) -> DevJournal | InstalledJournal | Unavailable:
    """Журнал этого процесса (Р-5): установка `df` — издатель; иначе dev."""
    if _publisher_present():
        df_run = env.get("DF_RUN_ID")
        if not df_run:
            return Unavailable(
                "периметр: установка df без контекста прогона (DF_RUN_ID)"
            )
        return InstalledJournal(df_run)
    return DevJournal(state_home, run_id, reread, policy_repository_id)
