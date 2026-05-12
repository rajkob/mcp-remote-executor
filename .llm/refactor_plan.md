# Change Title

<!-- Short refactor title -->

## Problem Being Solved

<!-- What is broken, inconsistent, or hard to change safely? -->

## Files Affected

| File | Why it changes |
|---|---|
| `credentials.py` | |
| `exec_log.py` | |
| `vms.py` | |
| `ssh_tools.py` | |
| `ping_tools.py` | |
| `monitor.py` | |
| `dashboard.py` | |
| `server.py` | |

## Contract Changes

| Contract item | Before | After |
|---|---|---|
| None — internal changes only | | |

## Execution Order

- [ ] Review `CONTRACTS.md` before touching any file
- [ ] Update `CONTRACTS.md` for any interface or data-shape change before editing the next file
- [ ] Refactor `credentials.py`
- [ ] Run `make refactor-check`
- [ ] Refactor `exec_log.py`
- [ ] Run `make refactor-check`
- [ ] Refactor `vms.py`
- [ ] Run `make refactor-check`
- [ ] Refactor `ssh_tools.py`
- [ ] Run `make refactor-check`
- [ ] Refactor `ping_tools.py`
- [ ] Run `make refactor-check`
- [ ] Refactor `monitor.py`
- [ ] Run `make refactor-check`
- [ ] Refactor `dashboard.py`
- [ ] Run `make refactor-check`
- [ ] Refactor `server.py` last
- [ ] Run `make refactor-check`

## Validation Step per File

| File | Validation step |
|---|---|
| `credentials.py` | Run `make refactor-check` immediately after the file changes |
| `exec_log.py` | Run `make refactor-check` immediately after the file changes |
| `vms.py` | Run `make refactor-check` immediately after the file changes |
| `ssh_tools.py` | Run `make refactor-check` immediately after the file changes |
| `ping_tools.py` | Run `make refactor-check` immediately after the file changes |
| `monitor.py` | Run `make refactor-check` immediately after the file changes |
| `dashboard.py` | Run `make refactor-check` immediately after the file changes |
| `server.py` | Run `make refactor-check` immediately after the file changes |

## Rollback Plan

1. Stop after the first failing file.
2. Revert the last file change.
3. Re-run `make refactor-check` to confirm the previous state is healthy.
4. Resume only after `CONTRACTS.md` and code agree again.

## LLM Instructions

- Read `CONTRACTS.md` before each file.
- Update `CONTRACTS.md` after each file when any public function, exception, or data structure changes.
- Run `make refactor-check` after each file.
- Fix any failures before proceeding.
- Never skip ahead in the execution order.
