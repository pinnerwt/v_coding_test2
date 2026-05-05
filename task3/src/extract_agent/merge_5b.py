"""Library re-export of task3/scripts/extract/_merge_5b.py."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "scripts" / "extract" / "_merge_5b.py"
_spec = importlib.util.spec_from_file_location("_merge_5b", _path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

merge_records = _mod.merge_records
merge_segments = _mod.merge_segments
fold = _mod.fold
