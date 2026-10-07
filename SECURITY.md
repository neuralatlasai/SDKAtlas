# Security policy

SDK Atlas analyzes Python source statically. Source scans must not import or
execute the target SDK. No API credentials are required. Inventory output may
contain source paths, docstrings, defaults, and route expressions; handle it with
the same access restrictions as the scanned source.

Only the latest released version receives security corrections. This project is
currently unreleased; no support period or response-time commitment is implied.

Report vulnerabilities through the hosting repository's private security
advisory channel when the owner has enabled it. No repository or private contact
has been configured in this workspace. Until a private channel is established,
do not disclose exploit details or sensitive source in a public issue. The
repository owner must establish a private reporting channel before publication.

Include the affected version, Python/platform details, a minimal sanitized
reproducer, trust boundary, and observed impact. Maintainers should assess
exploitability, preserve the reproducer as a regression test, document accepted
risk, and publish corrections as new immutable package versions.

`--latest` downloads a wheel through `pip` into a temporary target, with source
builds and dependency installation disabled. That operation needs network access
and trusts the configured package index and distribution. Prefer a reviewed
local source tree for untrusted packages. A temporary target isolates the install
location; it does not establish an operating-system security sandbox. Source
scans should run with the minimum filesystem access required.

CI uses read-only repository permissions, pinned action revisions, and an
installed dependency audit. Hosted secret scanning, dependency alerts, private
reporting, protected branches, and authorized reviewers remain administrator
controls described in [governance.md](docs/governance.md).
