# contracts/author-sandbox/v1

Контракт песочницы авторского агента: дизайн
`docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md` (rev 3.1, §14, §19),
план — `spec/author-agent-sandbox-tasks.md`.

| Файл | Что задаёт | Кто читает |
|---|---|---|
| `admitted.toml` | допущенные исполняемые файлы харнесса (`realpath`, sha256, macOS, шаблон профиля) | граница запуска `governance/author_launch` (TASK-003/005); пополняет оператор шагом H2 |
| `spawn-sites.toml` | исключения правила допустимых форм запуска процессов в `governance/` | тест инварианта `tests/test_author_sandbox_invariant.py` (TASK-005) |

Каталог — authority-root (`contracts/authority-root/v1/paths.env`) и входит в
`harness_files` spec-runner (запись `contracts`): правки мержит только человек.

**Граница гарантий (§19 п.3).** Песочница — с ограниченными гарантиями, не полная
изоляция агента (§17). Перечень мест запуска и инвариант делегирования защищают от
прямого и случайного обхода границы, но не доказывают безопасность произвольного
кода раннера: намеренный обход через незащищённый код — остаточный риск ревью PR.
