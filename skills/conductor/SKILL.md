---
name: conductor
description: Советчик флота — что сейчас главное, почему issue/пункт не закрыт, кто кого ждёт. Использовать при вопросах «почему не закрыт X», «что дальше», «кто чего ждёт».
---

# conductor — советчик (срез 0)

Ничего не пишет во флот. Команды — из devtools:

- `make conductor ARGS=status` — фокусы, очередь с `why`, «нужны решения»,
  циклы, ожидания, вопросы владельцу.
- `make conductor ARGS="why <node>"` — цепочка от узла до листьев с
  `need/actor`; `<node>` — `todo://<repo>/<id>`, `<repo>#<N>` или `<repo>!<N>`.
- `make conductor ARGS="plan --level 3"` — что conductor сделал бы на уровне 3
  (`launch?` — authority-root ещё не проверяется).
- `make conductor ARGS="record out/conductor/replays/<дата>"` — сохранить входы;
  `python -m conductor status --replay <dir>/inputs.json` — разбор без сети.

Отвечая пользователю, цитируй строку `why` и называй `partial`, если граф
неполон: «блокеров нет» на неполном графе не утверждается.
