"""Opt-in smoke стадии Need с НАСТОЯЩИМ discovery (спека §8).

Запуск: DEVTOOLS_DISCOVERY_SMOKE=1 uv run --frozen pytest -q
tests/test_discovery_smoke.py
Без переменной — skip: обычный pytest от соседа не зависит.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from governance import brief_input, interview as iv
from governance.ops import RealOps

pytestmark = pytest.mark.skipif(
    not os.environ.get("DEVTOOLS_DISCOVERY_SMOKE"),
    reason="opt-in: DEVTOOLS_DISCOVERY_SMOKE=1",
)

# Покрытие required-ключей customer-фрейма (discovery README): goals, personas,
# jobs, functions, nfr, constraints, success_metrics, out_of_scope — каждый
# закрывается ≥1 типизированной записью своего префикса (readiness §4 соседа
# считает секцию covered по наличию записи, не по тексту). FR/M дополнительно
# трассируются на существующие G/J, иначе GC-06/GC-07 держат gate=fail (код 10).
_ANSWERS = {
    "goals": {
        "text": "цель — сократить время приёмки прогона",
        "entries": [{"id": "G-01", "body": "сократить время приёмки прогона"}],
    },
    "personas": {
        "text": "основной пользователь — оператор прогона",
        "entries": [{"id": "P-01", "body": "оператор прогона, запускает spec-loop"}],
    },
    "jobs": {
        "text": "когда прогон готов, хочу подтвердить приёмку одной командой",
        "entries": [{
            "id": "J-01",
            "body": "когда прогон готов, хочу подтвердить приёмку одной командой",
        }],
    },
    "functions": {
        "text": "нужна одна кнопка приёмки",
        "entries": [{
            "id": "FR-01", "body": "одна команда запускает приёмку прогона",
            "Priority": "Must",
            "Acceptance": "прогон стартует одной командой и печатает статус",
            "traces": ["G-01", "J-01"],
        }],
    },
    "nfr": {
        "text": "приёмка не должна занимать больше минуты",
        "entries": [{
            "id": "NFR-01", "body": "приёмка отвечает не дольше минуты",
            "Acceptance": "команда завершается за 60 секунд на типовом прогоне",
            "traces": ["G-01"],
        }],
    },
    "constraints": {
        "text": "бюджет и сроки фиксированы текущим релизом",
        "entries": [{"id": "CON-01", "body": "изменения — в рамках текущего релиза"}],
    },
    "success_metrics": {
        "text": "метрика — доля прогонов, принятых одной командой",
        "entries": [{
            "id": "M-01", "body": "доля прогонов, принятых одной командой, растёт",
            "traces": ["G-01"],
        }],
    },
    "out_of_scope": {
        "text": "вне scope — ручной разбор логов",
        "entries": [{"id": "OUT-01", "body": "ручной разбор логов вне scope"}],
    },
}


def _answer_for(coverage_key: str) -> dict:
    return _ANSWERS.get(coverage_key, {"text": f"ответ по {coverage_key}"})


def test_real_discovery_customer_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCOVERY_HOME", str(tmp_path / "home"))
    ops = RealOps()
    cwd = str(tmp_path)
    reply = ops.discovery_start("customer", "owner/smoke", None, None, cwd)
    assert reply.code == 20, reply
    session = reply.envelope["next_action"]["session_id"]
    for _ in range(60):   # цикл по ВСЕМУ банку вопросов фрейма
        action = reply.envelope["next_action"]
        answer = tmp_path / "answer.yaml"
        answer.write_text(yaml.safe_dump(_answer_for(action.get("coverage_key", "")),
                                         allow_unicode=True), encoding="utf-8")
        answer_reply = ops._discovery(
            ["answer", "--session", session, "--role", "po",
             "--file", str(answer)], cwd,
        )
        assert answer_reply.code in (0, 20, 10, 11), answer_reply
        reply = ops.discovery_status(session, cwd)
        if reply.code != 20:
            break
    # 11 = gate pass, readiness incomplete — тоже терминал банка
    assert reply.code in (0, 11), reply
    out = tmp_path / "brief.md"
    assert ops.discovery_brief(session, str(out), cwd).code == reply.code
    text = out.read_text(encoding="utf-8")
    spec = iv.InterviewSpec("customer", "po", "owner/smoke", None, None)
    assert iv.brief_coordinate_findings(text, spec) == []
    # детерминизм рендера (§5.5): второй рендер побайтово равен
    out2 = tmp_path / "brief2.md"
    ops.discovery_brief(session, str(out2), cwd)
    assert out2.read_bytes() == out.read_bytes()
    if reply.code == 0:
        brief_input.inspect_brief(out)
