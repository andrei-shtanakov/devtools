# `dscl -plist . -read` records

Captured on macOS 26 (arm64) without root, 2026-10-21.

- `group-staff.plist` — `dscl -plist . -read /Groups/staff`, verbatim.
- `user-human-df-build.plist` — `dscl -plist . -read /Users/$USER` of a human
  account created through System Settings, reshaped as a foreign `df-build`:
  the name, real name, UUID, UID and timestamps are replaced; the password hash
  history, authentication authority, photo, certificates and SSO identifiers are
  removed. The multi-line plist value `accountPolicyData` is kept: it is the shape
  that broke the old line parser (review I1).
