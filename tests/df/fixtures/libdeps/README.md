# `linked_libraries` samples

- `otool-deps.txt` (`otool -L`), `otool-loadcmds.txt` (`otool -l`) — captured on macOS arm64 (2026-10-21) from a
  uv-managed CPython 3.10 binary; the binary path in `otool-deps.txt` is replaced
  with `/opt/x/bin/python3.10`, `otool-loadcmds.txt` is the tail of the load commands.
- `readelf-d.txt` — **synthesized** in the GNU binutils `readelf -d` format
  (no Linux host at implementation time); real capture is Task 13 CI.
