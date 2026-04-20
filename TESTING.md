# Testing & CI Reference

How testing is structured in this project, what is covered, and how the CI pipeline works.
Use this as a template for future projects.

---

## Core Principle

Every module has its own test file. Tests never touch real network, real SSH, or real files on disk — all external I/O is either mocked or redirected to a temporary directory. This means:

- Tests run in milliseconds — no SSH handshakes, no DNS, no sleep
- Tests are deterministic — same result every run, on any machine
- A failing test always points to your code, not to network conditions

---

## Test Framework

**pytest** is the runner. Tests are written using Python's built-in `unittest.TestCase` classes (compatible with pytest), grouped into classes by behaviour.

Run locally:
```bash
python -m pytest tests/ -v
```

The `-v` flag shows each test name and PASSED/FAILED individually.

---

## Test File Structure

Each test file mirrors one source module:

| Test file | Module tested | Tests |
|---|---|---|
| `test_credentials.py` | `credentials.py` | 15 |
| `test_exec_log.py` | `exec_log.py` | 22 |
| `test_init.py` | `init.py` | 16 |
| `test_monitor.py` | `monitor.py` | 35 |
| `test_ping_tools.py` | `ping_tools.py` | 12 |
| `test_server_ollama.py` | `server.py` (AI tools) | 16 |
| `test_server_tools.py` | `server.py` (all other tools) | 76 |
| `test_ssh_tools.py` | `ssh_tools.py` | 38 |
| `test_vms.py` | `vms.py` | 33 |
| `test_integration.py` | `server.py` (import smoke) | 4 |

**Total: 267 tests.**

---

## Three Patterns Used Throughout

### 1. Isolated temp directory for every test class

Every test class that touches files redirects all I/O to a fresh temp directory:

```python
def setUp(self):
    self._tmp = tempfile.TemporaryDirectory()
    os.environ["DATA_DIR"] = self._tmp.name   # redirect all file I/O here
    vms.init_empty()                          # create a clean vms.yaml

def tearDown(self):
    self._tmp.cleanup()                       # delete temp dir after each test
```

`DATA_DIR` is the single env var that controls where `vms.yaml`, `exec.log`, `credentials`, and `output/` live.
Pointing it at a temp dir means:
- No test can affect another test
- Nothing ever touches your real `data/` folder
- `tearDown` always cleans up, even if the test fails

### 2. Mocking external calls with `unittest.mock.patch`

SSH and network calls are replaced with controlled fakes:

```python
# Instead of opening a real SSH connection:
with patch("server.ssh_tools.ssh_exec", return_value={
    "alias": "web01", "ip": "10.0.0.1",
    "exit_code": 0, "stdout": "ok\n", "stderr": "", "elapsed_s": 0.1
}):
    result = server.run_command("web01", "uptime")

self.assertIn("ok", result)
```

`patch` temporarily replaces `ssh_exec` with a function that returns a fixed dict.
The code under test processes the response exactly as it would with a real host.
After the `with` block, the real function is restored automatically.

The same pattern is used for:
- `ping_tools.ping_host` → returns `{"up": True}` or `{"up": False}`
- `urllib.request.urlopen` → for webhook and credential tests
- `monitor._collect_host` → returns a fixed metrics dict
- `ssh_tools.ssh_exec_multi` → returns a list of per-host result dicts

### 3. Integration smoke test

`test_integration.py` does not test logic — it tests that the server imports cleanly and registers exactly the expected number of MCP tools:

```python
def test_tool_count(self):
    tools = asyncio.run(server.mcp.list_tools())
    self.assertEqual(len(tools), 29)

def test_expected_tool_names(self):
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    missing = EXPECTED_TOOLS - names
    self.assertFalse(missing, f"Missing expected tools: {missing}")
```

This acts as a **canary**:
- Add a tool but forget to update the count → CI fails immediately
- Delete a tool accidentally → CI fails immediately
- Import error or syntax error in server.py → CI fails before any SSH test runs

**Rule:** every time you add a tool, increment the count in `test_integration.py` and add its name to `EXPECTED_TOOLS`.

---

## What Is Tested

### Happy path
Correct input → expected output. Example: `run_command("web01", "df -h")` with a mocked successful SSH result returns a formatted string containing the output.

### Error paths
What happens when things go wrong:
- SSH raises `HostUnreachable`, `AuthFailure`, `CommandTimeout`
- `get_host("ghost")` raises `KeyError`
- Duplicate alias raises `DuplicateAlias`
- Bad CSV/JSON format returns an error string with `❌`

### Edge cases
- Empty input lists, `n=0`, `n=9999`
- Unknown alias falls back gracefully instead of crashing
- `tearDown` on an already-empty state does not raise

### Security (destructive command guard)
Each dangerous pattern is individually asserted to be blocked:

```python
def test_rm_recursive_root_blocked(self):
    reason = ssh_tools._check_destructive("rm -rf /")
    self.assertIsNotNone(reason)

def test_safe_command_returns_none(self):
    self.assertIsNone(ssh_tools._check_destructive("df -h"))
```

Patterns tested: `rm -rf /root/home`, `dd of=/dev/`, `mkfs`, `shutdown/halt/poweroff`, `reboot`, `init 0/6`, fork bomb, raw disk write.

### Concurrency (rate limiting)
Uses real threads to verify the per-host semaphore actually blocks:

```python
def test_semaphore_limits_concurrency(self):
    # Fill the semaphore to its limit, then verify a new acquire blocks
    ...
```

### Wall-clock timeout
Spawns a real thread that sleeps longer than the timeout, asserts `CommandTimeout` is raised within the window.

### Watch-set monitoring
- `watch_add/remove/clear` tested in isolation with no SSH needed
- `get_watched_metrics()` verified to call `_collect_host` only for watched aliases
- `_fetch_for_aliases()` verified to not modify the watch set as a side effect

---

## CI Pipeline

Defined in `.github/workflows/ci.yml`. Three sequential jobs:

```
push to main  ──►  test  ──►  docker-build  ──►  publish (:main)
push v* tag   ──►  test  ──►  docker-build  ──►  publish (:vX.Y.Z + :latest)
pull request  ──►  test  ──►  docker-build  (no publish)
```

Each job only starts if the previous job passed. A test failure stops the pipeline before Docker is even built.

### Job 1 — `test`

Runs on **Python 3.11 and 3.12 in parallel** using a matrix strategy:

```yaml
strategy:
  matrix:
    python-version: ["3.11", "3.12"]
steps:
  - pip install -r requirements.txt pytest
  - python -m pytest tests/ -v
```

Both matrix runs must pass. This catches code that accidentally uses a feature only available in one Python version.

### Job 2 — `docker-build`

Only starts after **both** matrix test runs pass. Does a full `docker build`. Catches:
- Missing `COPY` in `Dockerfile`
- Bad `RUN` command
- A dependency present in your virtual env but missing from `requirements.txt`

### Job 3 — `publish`

Runs on push to `main` or a `v*` tag. Requires two GitHub Secrets:
- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

| Trigger | Docker tags pushed to Hub |
|---|---|
| Push to `main` | `:main` |
| Push `v2.3.0` tag | `:v2.3.0` + `:latest` |

This means every merged PR automatically updates `:main` on Docker Hub.
Tagged releases update `:latest` and create a pinnable version tag.

---

## The Rule That Keeps This Maintainable

> **Every new tool or function gets tests in the same commit.**

The `test_tool_count` integration test enforces this mechanically — adding a tool without updating the count breaks CI before any human review.

The same discipline applies to every new function:
1. Write the function
2. Write a test class for it in the matching test file
3. Run `python -m pytest tests/ -v` locally — all must pass
4. Commit both together

If you find yourself writing "I'll add tests later" — don't. The mock infrastructure is already in place. Adding a new test class takes 10 minutes and saves hours of debugging in production.

---

## Checklist for Adding a New Feature

- [ ] Write the function / tool in the source module
- [ ] Add a test class in the matching `tests/test_*.py` file
  - [ ] `setUp` / `tearDown` with temp dir if file I/O is involved
  - [ ] Happy path test
  - [ ] At least one error/edge case test
- [ ] If a new MCP tool: update `test_integration.py` count and `EXPECTED_TOOLS`
- [ ] Run `python -m pytest tests/ -v` locally — all pass
- [ ] Commit source + tests together
- [ ] Push to `main` → CI runs automatically → Docker Hub `:main` updates
- [ ] For a release: `git tag vX.Y.Z && git push origin vX.Y.Z` → CI publishes `:vX.Y.Z` + `:latest`
