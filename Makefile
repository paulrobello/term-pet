.PHONY: build package test lint fmt typecheck checkall clean pre-commit pre-commit-update test-ollama ollama-report test-ollama-art test-ollama-art-resume ollama-art-report desktop desktop-clean dev

build:
	uv build

package:
	uv build

test:
	uv run pytest

lint:
	uv run ruff check --fix .

fmt:
	uv run ruff format .

typecheck:
	uv run pyright

checkall: fmt lint typecheck test

pre-commit:			# run pre-commit checks on all files
	pre-commit run --all-files

pre-commit-update:		# update pre-commit hooks
	pre-commit autoupdate

test-ollama:
	uv run scripts/test_ollama_commentary.py

ollama-report:
	uv run scripts/test_ollama_commentary.py --report-only

test-ollama-art:
	uv run scripts/test_ollama_art.py --fresh

test-ollama-art-resume:
	uv run scripts/test_ollama_art.py

ollama-art-report:
	uv run scripts/test_ollama_art.py --report-only

clean:
	rm -rf dist/ build/ .pytest_cache/ .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------------------
# macOS desktop pet (Swift/AppKit)
# ---------------------------------------------------------------------------

desktop:			## Build the macOS desktop pet (release)
	cd macos_desktop && swift build -c release

desktop-clean:			## Clean the Swift build artifacts
	cd macos_desktop && swift package clean

dev:				## Run tpet in macos-desktop mode
	uv run tpet --art-mode macos-desktop
