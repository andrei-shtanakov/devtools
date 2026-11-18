# Design of a personal project

You write the design of a personal project — a thing a person makes for
themselves. The basis is the brief `00-brief.md`: goals (G-NN), functions
(FR-NN) with acceptance criteria and five context lines (users,
authorization, network, local system, platform). Read it in full before
writing.

## What to write

The design in English, Markdown, as long as the project needs, no longer.
Write in English. There are no mandatory sections of the full chain
(personas, NFR, risks, metrics).

- **Structure:** modules and files of the project, commands and calls, data
  and where it is stored.
- **Decisions:** each one is a heading `#### D-NN: <decision>` (D-01, D-02, …
  in order, without gaps and repeats), under it 1–5 lines: what is decided,
  why, and a line `**Functions:** FR-01, FR-02` — which functions of the brief
  this decision serves. The decomposition tasks will later reference these
  D-NN.
- Every function of the brief is served by at least one decision; dropping a
  function of the brief is not allowed.
- Context decisions are explicit: platform, network, storage, authorization.
  If the brief says "doesn't matter" — choose the simplest option and name it.
- Do not invent functions that are not in the brief; do not narrow the
  functions and acceptance criteria and do not contradict the brief.
  Do not close gaps of the brief (the "To clarify" section) with a guess —
  list them at the end, in the `## Open questions` section.
- Reference only ids defined in the brief (G-NN, FR-NN) or in the design
  itself (D-NN).
