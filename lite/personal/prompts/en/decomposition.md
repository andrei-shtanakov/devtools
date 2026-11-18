# Decomposition of a personal project

You write the decomposition of a personal project: checks and tasks from
which the executor (spec-runner) will build the code. The bases are the
design `20-design.md` (decisions D-NN) and the brief `00-brief.md` (functions
FR-NN and their acceptance criteria). Read both in full before writing.

Write in English.

A criterion of the brief is called `FR-NN/k`, where k is the number of the
criterion in the "Acceptance criteria" list of that function, starting from
one: the second criterion of FR-01 is `FR-01/2`.

## Markup — strict, a machine reads it

A file without a YAML header, exactly two second-level sections in this
order: `## Checks`, then `## Tasks`. Inside the sections — no headings of
levels 1–3 (`#`, `##`, `###`): they cut the block.

### Section `## Checks`

Every check is a block:

```
#### BEH-01: <what it checks, in one line>
<1–3 lines: observable behaviour — what to do and what must result>
- **checked_by**: `kind: pytest` `target: tests/test_notes.py::test_add_prints_total`
**Criteria:** FR-01/1, FR-01/2
```

- The heading is exactly `#### BEH-NN: <title>`: BEH-01, BEH-02, … in
  order, a two-digit number, the colon and the title are mandatory.
- `**checked_by**` — exactly one line per check, both fields in backticks:
  `kind:` — `pytest` (an automatic test) or `manual` (a check by hand, only if
  it cannot be automatic); `target:` — the path to the test
  (`tests/<file>.py::<test>`) or, for `manual`, the path to a document with
  the steps.
- `**Criteria:**` — the criteria of the brief this check confirms, separated
  by commas. **Every criterion of every function of the brief is covered by
  at least one check.** Reference only existing criteria.

### Section `## Tasks`

Every task is a block:

```
#### DT-01: <what the task does> · type: implement · owner: dev
scenarios: [BEH-01]
depends_on: []
parallel_group: solo
**Design decisions:** D-01
<1–3 lines: what to do, the bounds of the task>
```

- The heading is exactly `#### DT-NN: <title> · type: implement · owner: dev`
  (separators are `·`); DT-01, DT-02, … in order.
- `scenarios: [BEH-01, BEH-02]` — the checks the task turns green. Every check
  belongs to **exactly one** task; a task has at least one check.
- A test file (`target` without `::…`) belongs to one task: checks of
  different tasks are in different files.
- `depends_on: [DT-01]` — tasks that must be done earlier; only those
  declared **above** in the document, without cycles; empty — `depends_on: []`.
- `parallel_group: solo` — always `solo`.
- `**Design decisions:**` — the design decisions D-NN the task implements; a
  task without a design decision is not a task of this project.
- Do not write other service lines (`delivers:`, `verifies:`,
  `delivered_by:`, `tdd_waiver:`).

Tasks follow the substance of the project: usually one to five, do not split
finely. Do not introduce obligations that are not in the design and the
brief.
