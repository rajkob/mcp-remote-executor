# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- `Modelfile` for Ollama — bakes in system prompt with `temperature 0` and `seed 42`
  for deterministic LLM tool-call behaviour
- **Canonical Command Mapping** table in `system_prompt.md` — prevents LLM from
  improvising command variants
- **Multi-Host Execution Rules** in `system_prompt.md` — requires `run_command_multi`
  over loops, documents parallel vs sequential mode
- Expanded `vms.yaml` template skeleton from 8 to 21 templates covering cpu-detail,
  processes, kernel, health-snap, running-services, connections, syslog, auth-log,
  cron-log, docker-ps, docker-stats, docker-df, largest-files
- `pyproject.toml` with ruff configuration so `make lint` passes cleanly on a fresh clone
- `CHANGELOG.md` (this file)
- `LICENSE` (MIT)

---

## [1.0.0] — 2025-05-01

### Added
- **29 MCP tools** — run commands, upload/download files, check reachability,
  manage credentials, bulk-import hosts (CSV/JSON), on-demand monitoring,
  per-host command history, structured log export (JSON/CSV), health check;
  optional AI-assisted analysis via local Ollama
- **Web dashboard** — live CPU / memory / disk / uptime for actively monitored hosts
  at `http://localhost:8765/dashboard`
- **On-demand monitoring** — `start_monitoring`, `stop_monitoring`,
  `monitoring_status` (polling only the hosts you care about right now)
- **Execution log panel** — recent SSH command history, filterable by host;
  exportable as JSON or CSV via `export_exec_log`
- **Bulk host import** — `import_hosts` accepts CSV or JSON
- **Webhook notifications** — `command_failed` / `host_down` events to Slack,
  Teams, or any HTTP endpoint via `WEBHOOK_URL`
- **Destructive command guard** — blocks `rm -rf /`, `dd`, `mkfs`, `shutdown`
  and similar by default; `force=True` overrides
- **SSH connection pool** — transports reused per host; capped at `MAX_POOL_SIZE`
  (default 50) with FIFO eviction
- **Per-host concurrency limit** — at most 3 concurrent SSH sessions per host by
  default (`MAX_CONCURRENT_PER_HOST`)
- **Encrypted credentials** — Fernet (AES-128-CBC + HMAC-SHA256), never plaintext
- **API key authentication** — optional `MCP_API_KEY` for shared/remote deployments
- **Deploy scripts** — `deploy.ps1` (Windows), `deploy.sh` (Linux/macOS),
  `deploy.py` (cross-platform with `--pull` / `--restart` / `--status` /
  `--reset-key` / `--version`)
- **VPN-friendly** — Docker `network_mode: host` so private subnets are reachable
- **Works with** — VS Code Copilot, Claude Desktop, Continue.dev, any SSE MCP client
- **GitHub Actions CI** — unit tests on Python 3.11 + 3.12, Docker build check,
  auto-publish to Docker Hub on push to `main` or version tag
- **CONTRACTS.md** — documented public function signatures for all 8 modules
- **Makefile** — `check-imports`, `check-contracts`, `lint`, `refactor-start`,
  `refactor-check`, `test-server`

[Unreleased]: https://github.com/rajkob/mcp-remote-executor/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/rajkob/mcp-remote-executor/releases/tag/v1.0.0
