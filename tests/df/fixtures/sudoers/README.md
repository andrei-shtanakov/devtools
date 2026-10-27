# `/etc/sudoers.d/df` and `sudo -n -l -U <name>` of the stage-2 roles

`expected.txt` — the exact text of `sudoers.render(Layout(Path("/")),
"runner")` (Task 3); `/usr/sbin/visudo -c -f` parses it OK (visudo 1.9.17p2,
macOS, as a regular user).

## Provisional — replace with CI capture

**Every `sudo-l-linux-*.txt` and `sudo-l-macos-*.txt` below is provisional —
replace with CI capture.** They are derived by hand from the sudo 1.9 `-l`
format (non-tty: no wrapping; the section headers and the global Defaults of
`../sudo-l/linux-some.out`, captured in CI run 40228831245; macOS global
Defaults from the stock `/etc/sudoers` of macOS 15), not captured:

- `sudo-l-<os>-df-exec.txt`, `sudo-l-<os>-df-review.txt` — a role after
  `install(render(layout, "runner"))`;
- `sudo-l-<os>-df-publisher.txt` — the role has no rule;
- `sudo-l-<os>-runner.txt` — the operator `runner`: its own rules (Linux:
  `(ALL) NOPASSWD: ALL`; macOS: also `%admin`'s `(ALL) ALL`) plus the
  generated ones.

Unknown until the capture: how sudo prints the `""` argument of
`df-review-run`, the order of the tags, and whether the command-specific
`Defaults!` binding is listed under "Runas and Command-specific defaults".

The capture is `test_sudoers_real` (`tests/df/test_acceptance_root_sudoers.py`,
marker `root`, CI df-stage1): it writes `$DF_STAGE1_OUT/sudoers/<name>.txt`
(stdout, `LC_ALL=C sudo -n -l -U <name>`), `<name>.rc` (exit code) and
`<name>.err` (stderr) for `df-exec`, `df-review`, `df-publisher`, `runner`,
plus `df` (the installed text). The `<R>` world puts the libexec paths under
`<R>/opt/df/libexec`; the test also writes `<name>.fixture.txt` with `<R>`
replaced by `/` and the host name by `ubuntu-ci` / `mac-mini` — those are the
files to copy here as `sudo-l-<os>-<name>.txt`.

## Synthesized (kept after the capture)

- `sudo-l-wrapped-df-review.txt` — `sudo-l-linux-df-review.txt` wrapped as
  sudo's line buffer wraps on a narrow terminal (Defaults continue with 4
  spaces, rules with 8); the parser must read it the same way.
