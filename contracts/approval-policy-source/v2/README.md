# approval-policy-source v2

Координаты доверенного источника политики для акта `review/v1`
(спека `docs/superpowers/specs/2026-10-12-approval-act-review-design.md` §6).

- `source.env` — репо, ветка, `BOOTSTRAP_ACTIVATION`, `POLICY_ADMINS`; пишет человек
  (шаг H2). Ключ `POLICY_ADMINS` (верхний регистр, как в v1 и `ssot_env`) — это
  `policy_admins` из спеки.
- `epochs.yaml` — журнал эпох доверия (§6.3); единственная мутация — атомарное
  закрытие последней эпохи + добавление следующей; валидатор —
  `governance/act_epochs.py`. Закрывающие метки времени (`valid_until` и прочие) —
  квотированные ISO-строки, не нативные YAML-даты.

Каталог под authority-root: PR, меняющий его, мержит человек. `v1/` остаётся для
legacy-пути.
