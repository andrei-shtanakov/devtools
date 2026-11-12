"""Вывод `dfl`: отказ — отдельным блоком, а не строкой под баннером."""

from __future__ import annotations

_INDENT = "               "  # ширина «  что сделать: »


def refusal(command: str, message: str, code: int = 2) -> str:
    """Блок отказа: заголовок с командой и кодом, «причина», «что сделать».

    Сообщения отказов пишутся как «причина — что сделать»: делим по первому
    « — ». Нет разделителя — только причина. Перенос строки в сообщении
    продолжается с отступом, чтобы блок не рассыпался.
    """
    cause, sep, action = message.partition(" — ")
    lines = ["", f"{command}: отказ (код {code})", _field("причина:", cause)]
    if sep:
        lines.append(_field("что сделать:", action))
    return "\n".join(lines)


def _field(label: str, text: str) -> str:
    body = text.strip().replace("\n", "\n" + _INDENT)
    return f"  {label:<13}{body}"
