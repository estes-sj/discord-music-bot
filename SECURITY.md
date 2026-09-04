# Security Policy

## Supported Versions

Security fixes are applied to the current development branch and the most recently published container image when practical. Older images and personal forks may not receive fixes.

## Reporting a Vulnerability

Do not open a public GitHub issue for a vulnerability, exposed credential, authorization-flow flaw, or data-disclosure concern.

Use GitHub's private vulnerability reporting feature for this repository, if it is enabled, at:

`https://github.com/estes-sj/discord-music-bot/security/advisories/new`

Include a clear description, affected component/version, reproduction steps, impact, and suggested mitigation when known. Do not include live bot tokens, Spotify Client Secrets, OAuth authorization codes, or user data. Use redacted examples or a newly created test credential instead.

If private reporting is unavailable, contact the repository maintainer privately through the GitHub profile rather than posting details publicly.

## Response Expectations

The maintainer will aim to acknowledge a report within seven days, assess severity, and coordinate a fix before public disclosure. Timelines may vary for third-party dependencies or platform issues. Please allow a reasonable remediation window before publishing details.

## Scope

Relevant reports include credential exposure, unauthorized access to guild data, improper handling of Spotify OAuth/client credentials, command permission bypasses, denial-of-service paths, and dependency or container vulnerabilities that materially affect a deployed bot.