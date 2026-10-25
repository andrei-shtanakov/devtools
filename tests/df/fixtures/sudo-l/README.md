# `sudo -n -l -U <name>` outputs

**`macos-none`, `linux-none`, `linux-some`: captured in CI run
https://github.com/andrei-shtanakov/devtools/actions/runs/40228831245** (head
8eca26a, workflow `df-stage1`, Task 13 R74), as root with `LC_ALL=C`:

- `macos-none` — macos-15 (sudo 1.9.13p2), `sudo -n -l -U df-build` after
  `create_build_user` (`test_sudo_rules_real`, artifact
  `sudo-l/macos-arm64-none.txt`);
- `linux-none` — ubuntu-24.04 (sudo 1.9.15p5), the same command
  (`sudo-l/linux-x86_64-none.txt`);
- `linux-some` — ubuntu-24.04, `sudo -n -l -U runner`, the runner's own
  rules (Step 0, `step0/sudo-l/runner.out`); the parser is called with the
  name `runner`.

The texts are byte-identical (after the same host-name edit) to the
captures of CI run
https://github.com/andrei-shtanakov/devtools/actions/runs/40232411657 (head
c57d613), the run the stage-1 evidence cites.

The only edit is the host name: the runner's name is replaced by `mac-mini`
/ `ubuntu-ci`. stderr was empty in all three, so `<case>.out` is stdout.

Synthesized, not captured (no real case exists to capture):

- `garbage` — exit 0 without either phrase;
- `exit-1` — sudo's own failure (`a password is required`, exit 1).

Each case is two files: `<case>.out` (combined output) and `<case>.rc` (exit
code).
