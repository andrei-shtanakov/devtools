# sudo's refusals of the stage-2a roles (`LC_ALL=C`)

**Captured in CI run
https://github.com/andrei-shtanakov/devtools/actions/runs/40853991482** (head
606e70a, workflow `df-stage1`, job `stage2a (<os>)`, stage 2a Task 14,
R14-2), as the role itself (a process of `df-exec` / `df-review`, no outer
`sudo`), by `sudo_refused` of `tests/df/test_acceptance_root_roles.py`, in the
world of `tests/df/roles_world.py` (production layout `/`):

- `macos` — macos-15 (artifact `df-stage2a-macos-15`,
  `sudo-diag/macos/<case>.{txt,rc}`);
- `linux` — ubuntu-24.04 (artifact `df-stage2a-ubuntu-24.04`,
  `sudo-diag/linux/<case>.{txt,rc}`).

The earlier runs 40852118817 (macOS) and 40853070812 (Linux) captured the
same bytes. `<case>.txt` is sudo's stderr, `<case>.rc` its exit code; stdout
was empty in every case. The test's own edits (`fixture_form`): `<R>/` paths
as `/` (none here: the world is `/`) and the host name as `mac-mini` /
`ubuntu-ci` (none here: no message names the host). The files are copied
byte for byte from the artifacts.

| case | command, as the role | class (`classify_sudo`) |
|---|---|---|
| `df-review-merge` | `sudo -n -u df-publisher …/df-publish merge` as `df-review` | `password-required` |
| `df-exec-publish-review` | `… df-publish publish-review` as `df-exec` | `password-required` |
| `df-exec-extra-argument` | `… df-publish perimeter-status extra` as `df-exec` | `password-required` |
| `df-exec-self-check` | `… df-publish self-check` as `df-exec` | `password-required` |
| `df-exec-sudo-E` | `sudo -E -n -u df-publisher … perimeter-status` as `df-exec` | `preserve-env` |
| `df-exec-preserve-env` | `sudo --preserve-env -n -u df-publisher …` as `df-exec` | `preserve-env` |

What the capture settled (finding F2 of the Task 14 report): on both
platforms sudo answers a command the role has no rule for with «a password
is required» (exit 1) — it asks for authentication before it judges the
command, and `-n` turns that into the refusal — never «is not allowed to
execute». Ruling R14-10a: that class counts as the refusal «sudo: нет правила
(запрос пароля под -n)» only with exit 1, empty stdout, no new line in the
publisher's journal, and sudo's own policy for the role -- its full listing
`sudo -n -l -U <role>` parsed by `sudoers.read_listing` -- granting no rule
for the same command while granting the control `df-publish
perimeter-status` (a grant of a form not judged exactly fails closed,
R14-14 M1); on every OS but macOS root's `sudo -n -l -U <role> -u
df-publisher <the same command>` must also exit 1 (its control 0), on macOS
it is evidence only (sudo 1.9.13 exits 1 even for a permitted command, CI
run 40876804216). Otherwise it is a wrong setup (`sudo_refused` and
`no_rule` of `tests/df/test_acceptance_root_roles.py`).

Synthesized, not captured (no real case exists to capture): the
`not-allowed` texts of `test_classify_sudo` in `tests/df/test_roles_world.py`.
