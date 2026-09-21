"""Сборка запроса и измерение его объёма (спека §4.1, D17).

Вход не усекается: превышенный потолок — отказ с числом. Усечение молча
меняет предмет проверки, а хэши при этом продолжают описывать полный вход.
"""

from __future__ import annotations

from dataclasses import dataclass

from governance.edge_check.inputs import PreparedInput
from governance.edge_check.rules import EdgeCheckError, RuleSet

#: Оценка токенов: utf-8 байты делим на 4. Метод объявлен в результате,
#: чтобы «поместилось/не поместилось» можно было перепроверить руками.
_BYTES_PER_TOKEN = 4

#: Версия шаблона запроса — часть `check_identity` (D8, находка I2). Правка
#: `build_prompt`, меняющая сборку запроса модели, обязана её поднять,
#: иначе результаты, снятые по прежней сборке, молча останутся действующими.
PROMPT_TEMPLATE_VERSION = 1


@dataclass(frozen=True)
class Measure:
    #: Единица измерения `size` (минорная находка: было "tokens", хотя
    #: `size` считается в utf-8 байтах — `estimate_tokens` и соседние поля
    #: самоочевидны по имени и в отдельной единице не нуждаются).
    unit: str
    method: str
    size: int
    estimate_tokens: int
    reserve_tokens: int
    limit_tokens: int


@dataclass(frozen=True)
class Prompt:
    text: str
    measure: Measure


def build_prompt(
    ruleset: RuleSet,
    prepared: PreparedInput,
    *,
    limit_tokens: int = 120_000,
    reserve_tokens: int = 8_000,
) -> Prompt:
    """Собрать промпт для ревьюера с контролем размера входа.

    Вход измеряется целиком: инструкция, правила, классы, документы,
    резерв под ответ. Если размер превышает потолок, выбрасывается ошибка
    без усечения (усечение меняет предмет проверки).

    Args:
        ruleset: Набор правил с инструкцией и деталями.
        prepared: Подготовленный вход с файлами и отсутствиями.
        limit_tokens: Потолок для входа (по умолчанию 120000).
        reserve_tokens: Резерв под ответ (по умолчанию 8000).

    Returns:
        Промпт с измерением объёма.

    Raises:
        EdgeCheckError: Если вход превышает потолок.
    """
    parts = [ruleset.instruction.strip(), "", "## Правила", ""]
    for item in ruleset.items:
        parts.append(f"- {item.id}: {item.text}")
    parts += ["", "## Классы находок", ""]
    parts.append(
        "Допустимые: " + ", ".join(sorted(ruleset.severity.known()))
    )
    parts += ["", "## Вход", ""]
    for f in prepared.files:
        parts += [f"### {f.role}: {f.path}", "", "```markdown", f.text, "```", ""]
    for a in prepared.absences:
        parts.append(
            f"Отсутствует (разрешено правилом {a.rule_id}): {a.path}"
        )
    text = "\n".join(parts)

    size = len(text.encode("utf-8"))
    estimate = size // _BYTES_PER_TOKEN
    if estimate + reserve_tokens > limit_tokens:
        raise EdgeCheckError(
            "input_too_large",
            f"вход {estimate} токенов + резерв {reserve_tokens} превышает "
            f"потолок {limit_tokens}; усечение запрещено",
        )
    return Prompt(
        text,
        Measure(
            unit="utf8-bytes",
            method="utf8-bytes/4",
            size=size,
            estimate_tokens=estimate,
            reserve_tokens=reserve_tokens,
            limit_tokens=limit_tokens,
        ),
    )
