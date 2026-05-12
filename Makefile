SHELL := /bin/bash

.PHONY: help check-imports check-contracts lint test-server refactor-start refactor-check

help: ## List available targets
	@awk 'BEGIN {FS = ": ## "}; /^[a-zA-Z0-9_.-]+:[[:space:]]+## / {printf "%-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check-imports: ## Import each project module
	@python -c "import vms; print('✓ imported vms')"
	@python -c "import credentials; print('✓ imported credentials')"
	@python -c "import exec_log; print('✓ imported exec_log')"
	@python -c "import ping_tools; print('✓ imported ping_tools')"
	@python -c "import ssh_tools; print('✓ imported ssh_tools')"
	@python -c "import monitor; print('✓ imported monitor')"
	@python -c "import dashboard; print('✓ imported dashboard')"
	@python -c "import server; print('✓ imported server')"

check-contracts: ## Import the documented public contracts
	@python -c "from vms import HostNotFound, DuplicateAlias, init_empty, load_hosts, get_host, get_all_hosts, get_hosts_by_project, get_hosts_by_tag, get_hosts_by_env, get_hosts_by_zone, resolve_target, write_host, delete_host, update_host, load_templates, write_template, delete_template, expand_template, write_hosts_bulk, format_hosts_table; print('✓ verified vms contracts')"
	@python -c "from ssh_tools import CredentialNotFound, HostUnreachable, AuthFailure, CommandTimeout, DestructiveCommandBlocked, close_all_connections, ssh_exec, ssh_exec_multi, sftp_upload, sftp_download; print('✓ verified ssh_tools contracts')"
	@python -c "from credentials import save_credential, get_credential, delete_credential, credential_exists, list_stored; print('✓ verified credentials contracts')"
	@python -c "from exec_log import append, read, clear; print('✓ verified exec_log contracts')"
	@python -c "from ping_tools import ping_host, ping_hosts, format_ping_results; print('✓ verified ping_tools contracts')"

lint: ## Install ruff if needed, then lint all Python files
	@python -m pip show ruff >/dev/null 2>&1 || python -m pip install ruff
	@python -m ruff check .

test-server: ## Start the stack and hit the SSE endpoint
	@docker compose up -d
	@sleep 4
	@curl localhost:8765/sse

refactor-start: ## Create a refactor branch and copy the active plan template
	@mkdir -p .llm
	@printf "Refactor branch name: "
	@read -r name; \
	if [ -z "$$name" ]; then \
		echo "Branch name is required."; \
		exit 1; \
	fi; \
	branch="refactor/$$name"; \
	if ! git check-ref-format --branch "$$branch" >/dev/null 2>&1; then \
		echo "Invalid branch name: $$branch"; \
		exit 1; \
	fi; \
	git checkout -b "$$branch"; \
	cp .llm/refactor_plan.md .llm/active_plan.md; \
	echo "✓ created .llm/active_plan.md"

refactor-check: ## Run import and contract checks for refactoring work
	@$(MAKE) check-imports
	@$(MAKE) check-contracts
	@echo "✅ Refactor checks passed."
