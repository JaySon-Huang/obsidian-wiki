"""Doc-contract tests for the portable source key contract (v2).

The contract lives in ``.skills/llm-wiki/SKILL.md``. These tests fail if a skill
doc regresses to teaching absolute stored keys, so the guidance cannot silently
drift back after the next upstream sync.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".skills"

# Literals that indicate a stored absolute path. Reference files under
# ``references/`` document raw tool-input JSON (e.g. a session's ``cwd`` field),
# which is genuinely absolute and not a stored key, so they are exempt.
FORBIDDEN_LITERALS = (
    "/absolute/",
    '"source_cwd":',
    "source_cwd=",
    '"path": "/',
    '"source_path": "/',
    'sources: ["/',
)

# Skills that carry (or link to) the key-format guidance.
CONTRACT_AWARE_SKILLS = (
    "wiki-status",
    "wiki-update",
    "wiki-ingest",
    "claude-history-ingest",
    "wiki-query",
)


def _skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.rglob("*.md"))


def test_skill_files_exist() -> None:
    files = _skill_files()
    assert files, "no skill markdown found — wrong path?"
    assert any(p.name == "SKILL.md" for p in files)


@pytest.mark.parametrize("literal", FORBIDDEN_LITERALS)
def test_no_absolute_stored_key_literals(literal: str) -> None:
    offenders = []
    for path in _skill_files():
        if "references" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if literal in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"absolute stored-key literal {literal!r} found in: {offenders}"


def test_llm_wiki_defines_the_contract() -> None:
    body = (SKILLS_DIR / "llm-wiki" / "SKILL.md").read_text(encoding="utf-8")
    assert "Source key contract (v2)" in body
    for marker in ("vault-relative", "~", "pseudo-key", "machine-portable"):
        assert marker in body, f"contract is missing the {marker!r} rule"


@pytest.mark.parametrize("skill", CONTRACT_AWARE_SKILLS)
def test_contract_aware_skills_reference_it(skill: str) -> None:
    body = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r"contract|llm-wiki/SKILL\.md", body, re.IGNORECASE), (
        f"{skill} neither states nor references the portable key contract"
    )


def test_wiki_status_manifest_example_keys_are_portable() -> None:
    body = (SKILLS_DIR / "wiki-status" / "SKILL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", body, re.S)
    manifest_blocks = [b for b in blocks if '"sources"' in b]
    assert manifest_blocks, "wiki-status manifest example JSON not found"
    sources = json.loads(manifest_blocks[0])["sources"]
    for key in sources:
        assert not key.startswith("/"), f"manifest example key is absolute: {key}"


def test_cli_docs_document_the_new_surface() -> None:
    """AGENTS.md requires docs/ to track new CLI surface (repo rule PR-005)."""
    cli = (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    assert "--key" in cli, "docs/cli.md does not document cache-update --key"
    assert "--source-hint" in cli, "docs/cli.md does not document cache-update --source-hint"
    assert "manifest.py" in cli and "migrate" in cli, (
        "docs/cli.md does not document scripts/manifest.py migrate"
    )
    for namespace in ("repo:", "url:", "agent:", "src:"):
        assert namespace in cli, f"docs/cli.md omits the {namespace!r} key namespace"


def test_contract_documents_every_key_namespace() -> None:
    body = (SKILLS_DIR / "llm-wiki" / "SKILL.md").read_text(encoding="utf-8")
    for namespace in ("repo:", "url:", "agent:", "src:"):
        assert namespace in body, f"llm-wiki contract omits the {namespace!r} namespace"


def test_architecture_docs_state_the_key_contract() -> None:
    arch = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    for marker in ("vault-relative", "~", "migrate"):
        assert marker in arch, f"docs/architecture.md is missing the {marker!r} rule"
