# Contributing to Remote Executor MCP Server

Thank you for considering a contribution! This guide describes how to get your
development environment up and running and what to check before opening a PR.

---

## Development setup

```bash
# 1. Clone the repository
git clone https://github.com/rajkob/mcp-remote-executor.git
cd mcp-remote-executor

# 2. Create a virtual environment (Python 3.11+)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt pytest

# 4. Generate a local master key (required by the test suite)
python init.py
```

---

## Running the tests

```bash
python -m pytest tests -q
```

All 267 tests must pass before opening a PR. The CI pipeline runs the same
command on Python 3.11 and 3.12.

---

## Code style

- Follow the existing style in each module (no strict linter enforced yet).
- Keep functions focused and add docstrings where the intent is non-obvious.
- Do not store credentials, keys, or real IP addresses in test fixtures.

---

## Adding a new MCP tool

1. Add the `@mcp.tool()` function to `server.py` in the appropriate section.
2. Update `system_prompt.md` — add a row to the *Intent → Tool Routing* table.
3. Add tests under `tests/test_server_tools.py` (mock SSH/file I/O).

---

## Submitting a pull request

1. Fork the repository and create a feature branch.
2. Make your changes and run `python -m pytest tests -q`.
3. Open a PR against `main`. Describe *what* you changed and *why*.
4. A CI run will confirm tests pass and the Docker image builds.

---

## Reporting bugs

Please open a GitHub issue and include:
- OS / Python version
- Docker version (if relevant)
- Minimal reproduction steps
- Relevant log output (`docker compose logs remote-executor`)

For security-sensitive issues, see [SECURITY.md](SECURITY.md).
