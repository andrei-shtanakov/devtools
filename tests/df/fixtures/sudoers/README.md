# `/etc/sudoers.d/df` and `sudo -n -l -U <name>` of the stage-2 roles

`expected.txt` — the exact text (since stage 2b Task 6 with the
`df-precheck` binding and rule of plan decision 5) of `sudoers.render(Layout(Path("/")),
"runner")` (Task 3). The text installed in CI (`sudoers/df` of the run below)
is the same apart from the `<R>` prefix of the paths.

**`sudo-l-<os>-<name>.txt`: captured in CI run
https://github.com/andrei-shtanakov/devtools/actions/runs/40454096166** (head
42a9c62, workflow `df-stage1`, stage 2a Task 3), as root with `LC_ALL=C`, by
`test_sudoers_real` (`tests/df/test_acceptance_root_sudoers.py`): the three
roles made by `roles.ensure_roles` in a `<R>` world, then
`install(render(layout, "runner"))`, then `sudo -n -l -U <name>`:

- `macos` — macos-15 (artifact `df-stage1-macos-15`, `sudoers/<name>.fixture.txt`);
- `linux` — ubuntu-24.04 (artifact `df-stage1-ubuntu-24.04`, the same files);
- `<name>`: `df-exec`, `df-review`, `runner` (the 2a `df-publisher`
  capture, then «not allowed», is now `df-precheck`'s, see below); `runner` is the
  runner's own account, the operator of the generated rules (its other
  rules are the image's: Linux `(ALL) NOPASSWD: ALL`; macOS
  `(ALL) NOPASSWD: ALL` and `(root) NOPASSWD: ALL`).

Exit code 0 and empty stderr in all eight captured in that run (`<name>.rc`,
`<name>.err`), the 2a `df-publisher` pair included.

The only edits (made by the test itself, `_as_fixture`; checked against the
raw `<name>.txt` of the same artifact with `diff`): the `<R>` prefix of the
libexec paths (`/private/var/tmp-df/df-acc-48511/sudoers`,
`/var/tmp-df/df-acc-34306/sudoers`) is removed, and the runner's host name is
replaced by `mac-mini` / `ubuntu-ci` in the section headers. The files are
copied byte for byte from the artifacts.

What the capture settled: sudo prints `df-review-run ""` literally; the
command binding `Defaults!…/df-publish !log_output` is listed under "Runas
and Command-specific defaults for <name>:"; the tags print as
`NOSETENV: NOPASSWD:`.

Synthesized, not captured (no real case exists to capture):

- `sudo-l-wrapped-df-review.txt` — `sudo-l-linux-df-review.txt` wrapped as
  sudo's line buffer wraps on a narrow terminal (Defaults continue with 4
  spaces, rules with 8). sudo never wraps output to a pipe, so this is only a
  tolerance check of the parser.

Synthesized in stage 2b Task 6 and **confirmed by capture in stage 2b Task 10**
(CI run https://github.com/andrei-shtanakov/devtools/actions/runs/41697032967,
head 8fd5d3d, the same test and artifacts `df-stage1-<os>`, `sudoers/<name>.fixture.txt`;
exit code 0 and empty stderr for all five names on both OSes): the captures
of `df-publisher` and `df-precheck` are byte for byte the synthesized files,
so the files below are now real captures:

- `sudo-l-<os>-df-publisher.txt` — `df-publisher`'s one rule; real sudo
  prints both `Defaults!` bindings under "Runas and Command-specific
  defaults" as `expected.txt` writes them (`command_timeout=180` included,
  unchanged) and the rule's tags as `NOSETENV: NOPASSWD:`.
- `sudo-l-<os>-df-precheck.txt` — a role without rules («not allowed to run
  sudo»).

The 2a captures of `df-exec`, `df-review` and `runner` predate the
`df-precheck` binding; tests add it by editing where it matters. Run
41697032967 shows that edit is the only difference: its captures of the three
differ from these files by exactly the line
`    Defaults!/opt/df/libexec/df-precheck command_timeout=180, …` (both OSes, checked
with `diff`).
