# `sudo -n -l -U <name>` outputs

**Synthesized, not captured.** Capturing a real output needs root (`sudo -l -U`
for another user is root-only), and Task 5 runs no root commands. The texts
follow sudo's documented messages:

- `*-none` — `User <name> is not allowed to run sudo on <host>.` (same wording
  on macOS and Linux sudo);
- `linux-some` — `Matching Defaults entries ...` followed by
  `User <name> may run the following commands on <host>:` and a rule line;
- `garbage` — exit 0 without either phrase;
- `exit-1` — sudo's own failure (`a password is required`, exit 1).

Each case is two files: `<case>.out` (combined output) and `<case>.rc` (exit
code). Real capture on both OSes is Task 13 CI (root marker); replace these
files with the captured ones there.
