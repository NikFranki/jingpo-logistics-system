.DEFAULT_GOAL := help
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
BE_PYTHON ?= $(if $(wildcard $(ROOT)be/.venv/bin/python),$(ROOT)be/.venv/bin/python,python3)
AGENT_PYTHON ?= $(if $(wildcard $(ROOT)agent/.venv/bin/python),$(ROOT)agent/.venv/bin/python,python3)

.PHONY: help check check-hooks check-be check-fe check-agent check-v11-integration handoff
help:
	@echo 'make check        Run BE, FE and Agent checks'
	@echo 'make check-hooks  Validate repository Codex hooks and safety rules'
	@echo 'make check-be     Temporary PostgreSQL DB, migrations and BE tests'
	@echo 'make check-fe     TypeScript, ESLint, component tests and production build'
	@echo 'make check-agent  Controlled Agent tests (no real model)'
	@echo 'make check-v11-integration  Isolated PostgreSQL BE/Agent HTTP write flow'
	@echo 'make handoff SESSION_ID=<id> INPUT=<exported-session.md>  Generate a session handoff with Codex'

check: check-hooks check-be check-fe check-agent

check-hooks:
	python3 -m json.tool "$(ROOT).codex/hooks.json" >/dev/null
	python3 -m unittest scripts.test_codex_hooks -v

check-be:
	"$(BE_PYTHON)" "$(ROOT)scripts/check.py" be

check-fe:
	cd "$(ROOT)fe" && npm run typecheck && npm run lint && npm test && npm run build

check-agent:
	"$(AGENT_PYTHON)" "$(ROOT)scripts/check.py" agent

check-v11-integration:
	RUN_V11_INTEGRATION=1 AGENT_PYTHON="$(AGENT_PYTHON)" "$(BE_PYTHON)" "$(ROOT)scripts/check.py" be

handoff:
	@test -n "$(SESSION_ID)" || (echo 'SESSION_ID is required' >&2; exit 2)
	@test -n "$(INPUT)" || (echo 'INPUT is required' >&2; exit 2)
	python3 "$(ROOT)scripts/handoff.py" --session-id "$(SESSION_ID)" --input "$(INPUT)"
