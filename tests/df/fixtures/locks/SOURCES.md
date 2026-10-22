# uv.lock goldens (Task 7a)

The goldens are a fact of `uv`, captured independently of `df/lockset.py`: for each case
`uv sync` installed the locked set into a fresh environment, and the expected JSON is read
from that environment's `*.dist-info` (`METADATA` Name/Version, raw `direct_url.json` or
`null`). Nothing is normalised. The capture script is not shipped (it lived in scratch only).

## Members

`<member>.uv.lock` is the exact bytes of `uv.lock` at the SHA below (`git archive`, checked
with `git show <sha>:uv.lock | cmp`). The SHAs are the origin default HEADs on 2026-10-22,
verified with `git ls-remote origin HEAD`. They are unrelated to the eventual `core` pins:
the live compatibility check stays with Task 14 (`df-installer-stage1-live`) (F4).

| Member | SHA | Source |
|---|---|---|
| devtools | 66304e821fc34666e980245d7df7a0f7955f8706 | https://github.com/andrei-shtanakov/devtools |
| discovery | 3ebed2d90580510be14c6d9d64d19ca8f8dca56d | https://github.com/andrei-shtanakov/discovery |
| discovery-toolkit | e5faa6776bbec08b5e6c05517a8d69ee10772185 | https://github.com/andrei-shtanakov/discovery-toolkit |
| disputatio | fc402fe22b4722813efd97fd46847e810accc33b | https://github.com/andrei-shtanakov/disputatio |
| steward | 007d00490599fa79484df648451988bf8939f25f | https://github.com/andrei-shtanakov/steward |
| spec-runner | d586fbb683158922bfd20091b6d4ed58f4583bfa | https://github.com/andrei-shtanakov/spec-runner |

## Cases

`<member>.<platform>.expected.json`, platform in `macos-arm64`, `linux-x86_64`. Fields:
`root_dir` (the `--project` path of the capture argv; tests must take `root_dir` from here,
not from `direct_url.json`), `python`, `groups`, `distributions` (sorted by name).

| File prefix | Member | `--group` flags | Packages |
|---|---|---|---|
| `devtools` | devtools | `governance` | 20 |
| `discovery` | discovery | none | 2 |
| `discovery-toolkit` | discovery-toolkit | none | 1 |
| `disputatio` | disputatio | none | 9 |
| `steward` | steward | none | 15 |
| `spec-runner` | spec-runner | none | 41 |
| `spec-runner.dev` | spec-runner | `dev` | 70 on macOS, 72 on Linux |
| `devtools.governance-conductor` | devtools | `governance`, `conductor` | 24 |

## Capture method

The source of each member is `git archive <sha>` extracted into a fresh directory at the
canonical path, then exactly the build's command (mirrors `df/build.py` `build_env` and
`_steps`):

```
env -i HOME=<scratch>/home PATH=<uv dir>:/usr/bin:/bin LANG=C.UTF-8 \
  UV_CACHE_DIR=<scratch>/cache UV_PYTHON_INSTALL_DIR=<scratch>/python \
  UV_LINK_MODE=copy UV_NO_CONFIG=1 UV_PYTHON_PREFERENCE=only-managed \
  XDG_CONFIG_HOME=<scratch>/config GIT_ATTR_NOSYSTEM=1 GIT_CONFIG_NOSYSTEM=1 \
  GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
  UV_PROJECT_ENVIRONMENT=<scratch>/venv/<case> \
  uv sync --locked --no-editable --no-default-groups [--group <g>]... \
    --project <root_dir> --python <python>
```

Python is installed with `uv python install --no-bin <key>` into the scratch
`UV_PYTHON_INSTALL_DIR`. Python is 3.12.13 on both platforms.

| | macOS | Linux |
|---|---|---|
| Python key | `cpython-3.12.13-macos-aarch64-none` | `cpython-3.12.13-linux-x86_64-gnu` |
| `<python>` | `/private/tmp/df7a-golden/python/<key>/bin/python3.12` | `/work/python/<key>/bin/python3.12` |
| `root_dir` | `/private/tmp/df7a-golden/src/<member>` | `/work/src/<member>` |
| uv | 0.11.23 (3cdf50e09 2026-06-19 aarch64-apple-darwin), binary sha256 `1cdf5520b22940540e5e0a650ed95a7674ab91f7465285f60a92716e269de08d` | 0.11.23, `uv-x86_64-unknown-linux-gnu.tar.gz` sha256 `e12c4cda2fe8c305510a78380a88f2c32a27e90cdcd123cefd2873388f0ebb5f` (equals the GitHub release `.sha256` file); extracted `uv` binary sha256 `8bec1053d461e6b62f062419e62daa65ab7430884829d6fafcb7157b76edb7a2` |
| Host | developer Mac, user account, scratch dirs only | local podman machine (applehv VM), amd64 emulation, container uid/gid 1000:1000, `uname -m` = `x86_64`, `sys.platform` = `linux` |

Linux image: `docker.io/library/ubuntu@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac`
(24.04 index digest; the linux/amd64 manifest is `sha256:a2d43bffc46670d7108bb3c0468c67c5557c4c92e83e1e34cbff1322ad1b94dc`,
used with `--platform linux/amd64`). A derived image added `git`, `ca-certificates` and `tar`
by `apt-get` (root only during the image build; git is needed for the two git dependencies),
the uv binary, and the source tarballs; the capture ran as uid 1000.

## Built from source (F10)

Every other reached package installs from a wheel.

- Git dependencies, built by their backends: `plan-fields` (dispatcher@f7f2cbc,
  `packages/plan-fields`) and `steward` (@b77ee0d), in the two `devtools` cases.
- The project root, because `--no-editable` builds a wheel: `discovery`, `disputatio`,
  `steward` (hatchling), `spec-runner` (setuptools; also in `spec-runner.dev`).

## Cross-platform differences

Only `root_dir` (and the root's `direct_url.json` `url`, which embeds it) differs, except
for `spec-runner.dev`, where Linux adds `jeepney 0.9.0` and `SecretStorage 3.5.0`
(`keyring` markers). No other case differs.

## exclude-newer fixture (Addendum A4)

Both made with `uv lock` 0.11.23 on macOS, scratch `UV_CACHE_DIR`, normal config. The lock
does not depend on the interpreter (`requires-python = ">=3.12"`).

- `exclude-newer/`: `[tool.uv] exclude-newer = "2024-01-01T00:00:00Z"`, `package = false`,
  dependency `iniconfig`. The lock records `[options] exclude-newer = "2024-01-01T00:00:00Z"`.
  - `uv lock --check` (no `UV_NO_CONFIG`) → rc 0, "Resolved 2 packages".
  - `UV_NO_CONFIG=1 uv lock --check` → rc 1, "Resolving despite existing lockfile due to
    removal of global exclude newer" and "The lockfile at `uv.lock` needs to be updated, but
    `--check` was provided. To update the lockfile, run `uv lock`." (looks like a stale lock).
- `stale/`: the same pyproject without `exclude-newer` and with a second dependency
  (`packaging`); `uv.lock` is the old lock of `iniconfig` alone, with no `[options]`.
  - `uv lock --check`, with or without `UV_NO_CONFIG=1` → rc 1, "The lockfile at `uv.lock`
    needs to be updated, but `--check` was provided." (a real stale reason).

## Not shipped

The capture script (scratch only) is not part of `df/` or `tests/` and nothing here refers to it.
