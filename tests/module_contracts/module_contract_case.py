from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = PROJECT_ROOT / "modules"
README_PATH = PROJECT_ROOT / "README.md"
MIGRATIONS_DIR = PROJECT_ROOT / "agent_app" / "storage" / "postgres" / "migrations"

MODULE_JOB_FIELDS = (
    "job_id",
    "module_name",
    "contour",
    "trigger_type",
    "universe_id",
    "instrument_ids",
    "horizons",
    "time_range",
    "input_refs",
    "config_ref",
    "run_mode",
    "idempotency_key",
    "priority",
)

REQUIRED_SECTION_MARKERS = (
    "## 1.",
    "## 2.",
    "## 3.",
    "## 4. Input classification",
    "## 5. Input contract",
    "## 6. External requests",
    "## 7. Processing rules",
    "## 8. Output classification",
    "## 9. Output contract",
    "## 10. Metrics / Records",
    "## 11. Stores",
    "## 12. TTL and freshness",
    "## 13. Failure policy",
    "## 14. Acceptance criteria",
    "Metric formulas / calculation rules",
    "Forbidden actions",
)

CLASSIFICATION_FIELDS = (
    "module_name",
    "module_type",
    "primary_contour",
    "execution_mode",
    "llm_usage",
)

VALID_STORE_DIRECTIONS = {"read", "write", "read/write"}
MODULE_JOB_OPTIONAL_MODULE_FILES = {
    "01_orchestration_module.md",
    "02_external_request_gateway_module.md",
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, marker: str) -> str:
    start = text.find(marker)
    if start == -1:
        return ""
    next_heading = text.find("\n## ", start + 1)
    return text[start:] if next_heading == -1 else text[start:next_heading]


def module_job_fields_section(text: str) -> str:
    marker = "Required `module_job` fields"
    start = text.find(marker)
    if start == -1:
        return ""
    next_heading = text.find("\n### ", start + 1)
    return text[start:] if next_heading == -1 else text[start:next_heading]


def extract_store_rows(stores_section: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    pattern = re.compile(r"^\| `([^`]+)` \| `([^`]+)` \|$", re.MULTILINE)
    for direction, store in pattern.findall(stores_section):
        if direction in VALID_STORE_DIRECTIONS:
            rows.append((direction, store))
    return rows


class ModuleContractCase(unittest.TestCase):
    module_file: str
    expected_module_name: str

    @property
    def path(self) -> Path:
        return MODULES_DIR / self.module_file

    @property
    def text(self) -> str:
        return read_text(self.path)

    def test_module_markdown_exists(self) -> None:
        self.assertTrue(self.path.exists(), f"Missing module spec: {self.module_file}")

    def test_declares_expected_module_name(self) -> None:
        self.assertIn(f"`module_name` | `{self.expected_module_name}`", self.text)

    def test_required_documentation_sections_exist(self) -> None:
        for marker in REQUIRED_SECTION_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.text)

    def test_classification_has_required_fields(self) -> None:
        classification = section(self.text, "## 2.")
        for field in CLASSIFICATION_FIELDS:
            with self.subTest(field=field):
                self.assertIn(f"`{field}`", classification)

    def test_module_job_contract_is_explicit(self) -> None:
        launch = section(self.text, "## 3.")
        if self.module_file in MODULE_JOB_OPTIONAL_MODULE_FILES:
            self.assertIn("не требует входного `module_job`", launch)
            return

        self.assertIn("`module_job`", launch)
        fields = module_job_fields_section(self.text)
        declared_fields = re.findall(r"^- `([^`]+)`$", fields, flags=re.MULTILINE)
        self.assertGreaterEqual(len(declared_fields), 3)
        self.assertIn("job_id", declared_fields)
        self.assertIn("run_mode", declared_fields)
        for field in declared_fields:
            with self.subTest(field=field):
                self.assertIn(field, MODULE_JOB_FIELDS)

    def test_input_and_output_contracts_are_json(self) -> None:
        self.assertIn("```json", section(self.text, "## 5. Input contract"))
        self.assertIn("```json", section(self.text, "## 9. Output contract"))

    def test_processing_rules_are_named_operations(self) -> None:
        rules = section(self.text, "## 7. Processing rules")
        operations = re.findall(r"^- `[^`]+`", rules, flags=re.MULTILINE)
        self.assertGreaterEqual(len(operations), 3)

    def test_store_policy_is_explicit(self) -> None:
        rows = extract_store_rows(section(self.text, "## 11. Stores"))
        self.assertGreaterEqual(len(rows), 1, "Store table must contain at least one policy row")
        for direction, store in rows:
            with self.subTest(store=store):
                self.assertIn(direction, VALID_STORE_DIRECTIONS)
                self.assertTrue(store.endswith("Store") or store.endswith("DB"))

    def test_acceptance_criteria_are_declared(self) -> None:
        criteria = section(self.text, "## 14. Acceptance criteria")
        items = re.findall(r"^- `[^`]+`", criteria, flags=re.MULTILINE)
        self.assertGreaterEqual(len(items), 3)

    def test_metric_formulas_are_declared(self) -> None:
        formulas = section(self.text, "Metric formulas / calculation rules")
        self.assertIn("`metric_name`", formulas)
        self.assertIn("Formula / rule", formulas)
        rows = re.findall(r"^\| `[^`]+` \| .+ \|$", formulas, flags=re.MULTILINE)
        self.assertGreaterEqual(len(rows), 1)

    def test_forbidden_actions_are_declared(self) -> None:
        forbidden = section(self.text, "Forbidden actions")
        items = re.findall(r"^- ", forbidden, flags=re.MULTILINE)
        self.assertGreaterEqual(len(items), 3)


def make_module_contract_test(module_file: str, expected_module_name: str) -> type[ModuleContractCase]:
    class GeneratedModuleContractTest(ModuleContractCase):
        pass

    GeneratedModuleContractTest.module_file = module_file
    GeneratedModuleContractTest.expected_module_name = expected_module_name
    safe_name = re.sub(r"[^A-Za-z0-9]+", "", expected_module_name)
    GeneratedModuleContractTest.__name__ = f"Test{safe_name}Contract"
    GeneratedModuleContractTest.__qualname__ = GeneratedModuleContractTest.__name__
    return GeneratedModuleContractTest
