"""Library re-export of task3/scripts/extract/_validate.py."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "scripts" / "extract" / "_validate.py"
_spec = importlib.util.spec_from_file_location("_validate", _path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

validate = _mod.validate
summary_lines = _mod.summary_lines
inspect_item = _mod.inspect_item
compare_golden = _mod.compare_golden
