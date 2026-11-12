"""Вывод `dfl`: отказ — отдельным блоком, а не строкой под баннером."""

from __future__ import annotations

_SEP = " — "
_INDENT = "               "  # ширина «  что сделать: »


def refusal(command: str, message: str, code: int = 2) -> str:
    """Блок отказа: заголовок с командой и кодом, «причина», «что сделать».

    Сообщения отказов пишутся как «причина — что сделать»: делим по первому
    « — » вне кавычек — подставленные данные (тема `{subject!r}`, путь) сами
    могут нести тире. Нет разделителя — только причина. Перенос строки в
    сообщении продолжается с отступом, чтобы блок не рассыпался.
    """
    cause, sep, action = _split_outside_quotes(message)
    lines = ["", f"{command}: отказ (код {code})", _field("причина:", cause)]
    if sep:
        lines.append(_field("что сделать:", action))
    return "\n".join(lines)


def _split_outside_quotes(message: str) -> tuple[str, str, str]:
    quote = ""
    for i, ch in enumerate(message):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif message.startswith(_SEP, i):
            return message[:i], _SEP, message[i + len(_SEP) :]
    return message, "", ""


def _field(label: str, text: str) -> str:
    body = text.strip().replace("\n", "\n" + _INDENT)
    return f"  {label:<13}{body}"
