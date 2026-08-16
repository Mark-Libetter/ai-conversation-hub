from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "conversation-hub"
    / "scripts"
    / "validate_project_contract.py"
)
SPEC = importlib.util.spec_from_file_location("validate_project_contract", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


HANDOFF = """---
task_id: T-001
status: active
owner_role: integrator
updated: 2026-08-16
---

# T-001: Fixture

## Goal
Ship one result.
## Scope
- fixture
## Do not touch
- vendor data
## Inputs
- repository
## Acceptance criteria
- [ ] validation passes
## Git delivery
- repo_root: C:/fixture
- base_branch: origin/main
- start_commit: abc1234
- work_branch: feature/test
- commit_policy: required
- push_policy: approval_required
- push_target: origin/feature/test
- integration_owner: user
- end_commit: pending
- dirty_state: clean
## Attempts and evidence
- none
## Files changed
- none
## Current status
Active.
## Next step
Validate.
"""


class ProjectContractTests(unittest.TestCase):
    def make_root(self, handoff: str = HANDOFF) -> Path:
        temp = tempfile.TemporaryDirectory(prefix="hub-contract-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name in ("PROJECT.md", "DECISIONS.md"):
            (root / name).write_text(f"# {name}\n", encoding="utf-8")
        (root / "TASKS.md").write_text("# Tasks\n\n| T-001 | active |\n", encoding="utf-8")
        (root / "handoffs").mkdir()
        (root / "handoffs" / "T-001.md").write_text(handoff, encoding="utf-8")
        return root

    def test_valid_contract(self) -> None:
        result = VALIDATOR.validate(self.make_root())
        self.assertTrue(result["valid"], result["errors"])

    def test_windows_line_endings_are_valid(self) -> None:
        result = VALIDATOR.validate(self.make_root(HANDOFF.replace("\n", "\r\n")))
        self.assertTrue(result["valid"], result["errors"])

    def test_missing_git_policy_is_rejected(self) -> None:
        handoff = HANDOFF.replace("- push_policy: approval_required\n", "")
        result = VALIDATOR.validate(self.make_root(handoff))
        self.assertFalse(result["valid"])
        self.assertIn("T-001.md: missing Git delivery field push_policy", result["errors"])


if __name__ == "__main__":
    unittest.main()
