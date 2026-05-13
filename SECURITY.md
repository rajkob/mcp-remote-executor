# Security Policy

## Supported versions

Only the latest release on the `main` branch receives security fixes.

| Version | Supported |
|---------|-----------|
| latest  | ✅ yes    |
| older   | ❌ no     |

---

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report security issues by emailing the maintainer directly or by using
[GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability).

Include:
- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Any suggested mitigations

You can expect an acknowledgement within 72 hours and a fix or mitigation
plan within 14 days for confirmed issues.

---

## Security design notes

- **Encrypted credentials** — SSH passwords are stored with Fernet
  (AES-128-CBC + HMAC-SHA256). The master key (`CRED_MASTER_KEY`) must be
  kept secret and never committed to source control.
- **API key authentication** — Set `MCP_API_KEY` in `.env` to require the
  `X-MCP-Key` header on every request. Recommended for shared or remote
  deployments.
- **SSH host key verification (TOFU)** — On first connection to a new host
  the server stores its key in `data/known_hosts`. Subsequent connections
  verify the key matches, protecting against MITM attacks. Remove a stale
  entry from `data/known_hosts` only after verifying the host key changed
  legitimately.
- **Destructive command guard** — `rm -rf /`, `dd`, `mkfs`, `shutdown`, and
  similar commands are blocked by default. Pass `force=True` only after
  explicit user confirmation.
- **No secrets in logs** — Credential values are never written to `exec.log`
  or returned in tool responses.
