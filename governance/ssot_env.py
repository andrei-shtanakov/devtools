"""Разбор SSOT-файлов формата KEY=VALUE — python-половина.

Формат читают ДВЕ половины: этот модуль и функция `ssot_key` в `merge-pr.sh`.
Поэтому **поведение на битом входе — часть формата**, а не деталь реализации:
разойдясь на нём, половины разойдутся молча, и SSOT перестанет быть SSOT ровно
там, где заводился (ревью #183, круг 6 — python брал первое вхождение ключа,
shell последнее).

Правила, одинаковые в обеих половинах:

* строка обрезается по краям; пустая и начинающаяся с `#` игнорируются;
* определение ключа — строка, начинающаяся ровно с `KEY=` (после обрезки
  ведущих пробелов). `KEY =…`, `export KEY=…`, `key=…` определениями НЕ
  считаются: формат минимален намеренно, и всё, что на определение лишь
  похоже, оставляет ключ ненайденным — то есть ведёт к отказу;
* значение — остаток строки, обрезанный по краям; пробелы внутри значения
  сохраняются (перечни разделяют им элементы);
* **дубль ключа — отказ обеих половин.** Файл с дублем битый, и выбирать за
  человека, какое из двух значений настоящее, нельзя: именно такой молчаливый
  выбор и развёл половины;
* ключ не найден или значение пусто — отказ.

Fail-closed целиком: любой отказ разбора — исключение, а не пустое значение.
Пустое значение означало бы «правила нет», то есть снятие защиты молчанием.
"""

from __future__ import annotations

from pathlib import Path


def _definition_lines(text: str, key: str) -> list[str]:
    """Строки-определения `key` (по правилам формата выше)."""
    prefix = f"{key}="
    found = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(prefix):
            found.append(stripped[len(prefix):].strip())
    return found


def read_key(path: Path, key: str, what: str) -> str:
    """Значение `key` из SSOT-файла `path`; `what` — имя файла для диагностики.

    Отказ (RuntimeError) на: недоступном файле, отсутствии ключа, дубле
    ключа, пустом значении.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # noqa: TRY003 — путь важнее классификации
        raise RuntimeError(f"{what} недоступен: {path} ({exc})") from exc
    found = _definition_lines(text, key)
    if len(found) > 1:
        raise RuntimeError(
            f"в {path} ключ {key} определён {len(found)} раз — файл битый; "
            "какое значение настоящее, решает человек, не разбор"
        )
    if not found:
        raise RuntimeError(f"в {path} нет {key}")
    if not found[0]:
        raise RuntimeError(f"в {path} нет непустого {key}")
    return found[0]
