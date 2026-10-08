"""Opt-in smoke engineer-маршрута с НАСТОЯЩИМ discovery (спека need-stage §11.7).

Запуск: DEVTOOLS_DISCOVERY_SMOKE=1 uv run --frozen --group governance pytest -q
tests/test_engineer_route_smoke.py
Без переменной — skip: обычный pytest от соседа не зависит. Сосед берётся
там же, где его берёт `RealOps` (`<devtools>/../discovery`).

- T51: подписанный бриф → `start --upstream --session-id` → банк вопросов →
  `brief`: итоговый бриф ссылается ровно на `upstream.md`.
- T38: блокировка прогона живёт, пока жив настоящий `uv → discovery`,
  даже когда процесс, взявший её, убит.
- T52: два одновременных `start` с одним id и разными upstream без нашей
  блокировки — фиксирует поведение соседа для discovery#63 п.3 (не гейт).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from governance import brief_input, run_lock
from governance import interview as iv
from governance import run_state as rs
from governance.ops import DEVTOOLS_ROOT, RealOps

pytestmark = pytest.mark.skipif(
    not os.environ.get("DEVTOOLS_DISCOVERY_SMOKE"),
    reason="opt-in: DEVTOOLS_DISCOVERY_SMOKE=1",
)

FIX = Path(__file__).parent / "fixtures" / "discovery_approval"
SIGNED = (FIX / "signed-brief.md").read_text(encoding="utf-8")
MUST_FR = ", ".join(f"FR-0{i}" for i in range(1, 9))
_ANSWERS = {
    # GC-05 ищет id Must-FR upstream'а в ТЕЛЕ брифа; текст ответа о
    # feasibility в тело не рендерится — вердикт несёт запись системы.
    "systems": {
        "text": "система — раннер",
        "entries": [{"id": "S-01", "body": f"раннер; выполнимы {MUST_FR}"}],
    },
    "interfaces": {
        "text": "CLI",
        "entries": [{"id": "IF-01", "body": "CLI раннера", "traces": ["S-01"]}],
    },
    "constraints": {
        "text": "без новых зависимостей",
        "entries": [{"id": "CON-01", "body": "без новых зависимостей"}],
    },
    "arch_preferences": {
        "text": "stdlib",
        "entries": [{"id": "AP-01", "body": "stdlib", "traces": ["S-01", "CON-01"]}],
    },
    "risks": {"text": "гонки", "entries": [{"id": "RK-01", "body": "гонки"}]},
    "feasibility_review": {"text": f"{MUST_FR} выполнимы"},
}


def _upstream(tmp_path: Path) -> str:
    path = tmp_path / "run" / "upstream.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SIGNED, encoding="utf-8")
    return str(path)


def test_real_discovery_engineer_loop(tmp_path: Path, monkeypatch) -> None:  # T51
    monkeypatch.setenv("DISCOVERY_HOME", str(tmp_path / "home"))
    ops = RealOps()
    cwd = str(tmp_path / "run")
    upstream = _upstream(tmp_path)
    reply = ops.discovery_start(
        "engineer", "owner/smoke", None, upstream, cwd, session_id="s-smoke-e"
    )
    assert reply.code == 20, reply
    assert reply.envelope["next_action"]["session_id"] == "s-smoke-e"
    for _ in range(60):
        action = reply.envelope["next_action"]
        answer = tmp_path / "answer.yaml"
        payload = _ANSWERS.get(
            action.get("coverage_key", ""), {"text": f"ответ {MUST_FR}"}
        )
        answer.write_text(yaml.safe_dump(payload, allow_unicode=True), "utf-8")
        got = ops._discovery(
            ["answer", "--session", "s-smoke-e", "--role", "po", "--file", str(answer)],
            cwd,
        )
        assert got.code in (0, 20, 10, 11), got
        reply = ops.discovery_status("s-smoke-e", cwd)
        if reply.code != 20:
            break
    out = Path(cwd) / "brief.md"
    final = ops.discovery_brief("s-smoke-e", str(out), cwd)
    assert final.code == 0, final.envelope.get("findings")
    meta = yaml.safe_load(out.read_text(encoding="utf-8").split("---\n")[1])
    assert meta["traces_to"] == [iv.UPSTREAM_NAME]
    spec = iv.InterviewSpec("engineer", "po", "owner/smoke", iv.UPSTREAM_NAME, None)
    assert iv.brief_coordinate_findings(out.read_text(encoding="utf-8"), spec) == []
    # E1 проходит: бриф рядом с durable upstream.md — source-слой из двух файлов.
    source = brief_input.inspect_brief(out)
    assert source.source_paths == (
        "00-discovery/brief.md",
        "00-discovery/upstream.md",
    )


_HOLDER = """
import subprocess, sys
from pathlib import Path
from governance import run_lock, run_state as rs
rs.RUNS_ROOT = Path(sys.argv[1])
read_fd, project, session = int(sys.argv[2]), sys.argv[3], sys.argv[4]
with run_lock.run_lock("r-smoke") as lock:
    child = subprocess.Popen(
        ["uv", "run", "--frozen", "--project", project, "discovery", "answer",
         "--session", session, "--role", "po", "--file", "-"],
        stdin=read_fd, pass_fds=(lock.fd,), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(child.pid, flush=True)
    child.wait()
"""


def test_lock_lives_while_real_discovery_lives(
    tmp_path: Path, monkeypatch
) -> None:  # T38
    monkeypatch.setenv("DISCOVERY_HOME", str(tmp_path / "home"))
    runs = tmp_path / "runs"
    monkeypatch.setattr(rs, "RUNS_ROOT", runs)
    reply = RealOps().discovery_start(
        "customer", "owner/smoke", None, None, str(tmp_path)
    )
    session = reply.envelope["next_action"]["session_id"]
    read_fd, write_fd = os.pipe()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _HOLDER,
            str(runs),
            str(read_fd),
            str(DEVTOOLS_ROOT.parent / "discovery"),
            session,
        ],
        pass_fds=(read_fd,),
        stdout=subprocess.PIPE,
        text=True,
        cwd=DEVTOOLS_ROOT,
        env={**os.environ, "DISCOVERY_HOME": str(tmp_path / "home")},
    )
    os.close(read_fd)
    assert holder.stdout is not None
    holder.stdout.readline()  # uv → discovery запущен и ждёт stdin
    time.sleep(1.0)
    holder.send_signal(signal.SIGKILL)  # родитель, взявший блокировку, убит
    holder.wait()
    with pytest.raises(run_lock.LockBusy):  # discovery жив — блокировка держится
        with run_lock.run_lock("r-smoke"):
            pass
    os.close(write_fd)  # EOF: discovery дочитывает stdin и выходит
    deadline = time.monotonic() + 30
    while True:
        try:
            with run_lock.run_lock("r-smoke"):
                break
        except run_lock.LockBusy:
            assert time.monotonic() < deadline, "блокировка не отпущена после выхода"
            time.sleep(0.2)


def _signed_variant(name: str) -> str:
    """Отдельный корректно подписанный upstream (своя подпись `stamp` соседа
    над своими байтами): T52 требует РАЗНЫЕ upstream у двух вызовов."""
    from governance import discovery_approval as da

    draft = (FIX / "draft-brief.md").read_text(encoding="utf-8") + "\n" * (
        1 if name == "a" else 2
    )
    event = da._approval.MergeEvent(
        "andrei-shtanakov", "2026-10-08T10:00:00Z", "c" * 40
    )
    return da._approval.stamp(draft, event)


@pytest.mark.xfail(
    strict=False, reason="discovery#63 п.3: достройка резервации не эксклюзивна"
)
def test_concurrent_start_same_id_is_exclusive(
    tmp_path: Path, monkeypatch
) -> None:  # T52
    home = tmp_path / "home"
    monkeypatch.setenv("DISCOVERY_HOME", str(home))
    project = str(DEVTOOLS_ROOT.parent / "discovery")
    procs = []
    for name in ("a", "b"):
        upstream = tmp_path / name / "upstream.md"
        upstream.parent.mkdir()
        upstream.write_text(_signed_variant(name), encoding="utf-8")
        procs.append(
            subprocess.Popen(
                [
                    "uv",
                    "run",
                    "--frozen",
                    "--project",
                    project,
                    "discovery",
                    "start",
                    "--frame",
                    "engineer",
                    "--target",
                    "owner/smoke",
                    "--upstream",
                    str(upstream),
                    "--session-id",
                    "s-race",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    codes = sorted(p.wait() for p in procs)
    print(f"коды двух одновременных start: {codes}")
    assert codes.count(20) == 1  # эксклюзивность — то, о чём просит discovery#63
