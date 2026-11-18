# edge-check v1-en — English rule catalog of the personal path

The English mirror of `../v1/` for the personal path of `dfl` only (run
language `en`, devtools#769). It holds the two personal edges
(`personal-design-vs-brief`, `personal-decomposition-vs-design-brief`) and the
reviewer instruction; enterprise edges stay in `v1` only.

- `response-schema.json` is a byte copy of `v1`.
- The rules are translations: every non-text field (`edge`, `subject_role`,
  `basis_roles`, item ids and their order, `severity`, `applicability`) is
  equal to `v1`; only `items[].text` differs.
- `instruction.md` is a translation of the `v1` instruction plus one line
  asking for English reasons and findings.

The check identity of `v1-en` differs from `v1` (instruction and rule texts
are part of it). A change in `v1` of a personal edge or of the instruction
must be mirrored here; `tests/test_lite_english_prompts.py` checks the
structure and the schema copy.
