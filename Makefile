.DEFAULT_GOAL := help
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
BE_PYTHON ?= $(if $(wildcard $(ROOT)be/.venv/bin/python),$(ROOT)be/.venv/bin/python,python3)
AGENT_PYTHON ?= $(if $(wildcard $(ROOT)agent/.venv/bin/python),$(ROOT)agent/.venv/bin/python,python3)

.PHONY: help check check-be check-fe check-agent handoff
help:
	@echo 'make check        Run BE, FE and Agent checks'
	@echo 'make check-be     Temporary PostgreSQL DB, migrations and BE tests'
	@echo 'make check-fe     TypeScript, ESLint and production build'
	@echo 'make check-agent  Controlled Agent tests (no real model)'
	@echo 'make handoff SESSION_ID=<id> INPUT=<exported-session.md>  Generate a session handoff with Codex'

check: check-be check-fe check-agent

check-be:
	"$(BE_PYTHON)" "$(ROOT)scripts/check.py" be

check-fe:
	cd "$(ROOT)fe" && npm run typecheck && npm run lint && npm run build

check-agent:
	"$(AGENT_PYTHON)" "$(ROOT)scripts/check.py" agent

handoff:
	@test -n "$(SESSION_ID)" || (echo 'SESSION_ID is required' >&2; exit 2)
	@test -n "$(INPUT)" || (echo 'INPUT is required' >&2; exit 2)
	python3 "$(ROOT)scripts/handoff.py" --session-id "$(SESSION_ID)" --input "$(INPUT)"
