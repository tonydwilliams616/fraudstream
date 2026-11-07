<!--
docs/security.md - how container images are scanned, which findings are
accepted and why, and the hardening work still to do.
-->
# Security

## Container image scanning

Every image is scanned twice:

| Scanner | When | Covers | Blocks? |
|---|---|---|---|
| Trivy 0.75.0 (`images.yml`) | CI, after the smoke test and **before** the push to ECR | OS packages and Python packages | Yes: any HIGH or CRITICAL finding **that has a fix available** fails the build, so a vulnerable image never reaches ECR |
| ECR basic scan | On push | OS packages only | No: findings are shown in ECR |

**Policy**
- **Fixable HIGH/CRITICAL:** blocked. Fix by rebuilding (the Dockerfile applies OS updates) or by upgrading the package.
- **Unfixed (no patch released upstream):** reported, not blocked. They clear when Debian ships a fix and the image is rebuilt. Every merge to `main` rebuilds the image with the latest patches.
- **Trivy itself** runs from the official image pinned by digest, not via `trivy-action` (whose tags were hijacked in CVE-2026-33634). It is upgraded deliberately: look up the new digest, update `TRIVY_IMAGE`, open a PR. Dependabot can't see this pin because it sits inside a `run:` step.

**Why the two scanners disagree.** They use different severity sources. Trivy prefers the distribution's own rating (Debian); ECR uses another source's rating, typically NVD. A CVE Debian rates MEDIUM can show as HIGH in ECR.

## Accepted risk: simulator image (November 2026)

Base `python:3.12-slim-trixie` (Debian 13.7). Trivy: 0 fixable HIGH/CRITICAL. The unfixed findings, and why none of them is reachable in this container:

| Packages | Issue | Why it's not reachable here |
|---|---|---|
| util-linux, mount, login, bsdutils and their libraries | mount helper / `nsenter` privilege issues | Needs root and mount privileges. The task runs as uid 10001 on Fargate (no privileged containers) with a read-only root filesystem, and nothing calls these tools |
| libsystemd0, libudev1 | `systemd-homed` privilege escalation | systemd doesn't run in the container |
| libsqlite3-0 | crafted database / FTS5 / changesets | The app never uses SQLite |
| ncurses, perl-base | terminal UI / tar parsing | Not used by the app |
| zlib1g, CVE-2026-85091 | `gz_vacate()` overflow | gzip *file* API; Python's `zlib` module uses the in-memory functions |
| zlib1g, CVE-2026-76844 | titled *webpack-dev-middleware* | False positive: a JavaScript package mismatched to zlib in the database |
| glibc, CVE-2026-19499 and CVE-2026-5435 | `strfmon` overflow; DNS TSIG handling | Rated below HIGH by Debian (HIGH in ECR). Unfixed; tracked |

Review this list whenever the base image changes, or when a finding gains a fix (the CI gate will catch that automatically).

## Hardening backlog

| Item | Why | Status |
|---|---|---|
| Distroless runtime image | Removes about 40 of the unfixed findings by not shipping util-linux, ncurses, perl, systemd libraries or a shell. Trade-offs: moves to Debian's Python 3.13, the smoke test needs reworking, no shell for debugging | Planned |
| Pin GitHub Actions by commit SHA | A moved tag can't change what runs in CI | Planned |
| Dependabot (Actions, Docker base images, pip) | Automatic update PRs | Planned |
| ECR enhanced scanning (Amazon Inspector) | Continuous rescans and Python package coverage in AWS | Optional |
