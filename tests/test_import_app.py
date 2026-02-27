import importlib
import sys
from pathlib import Path


def test_app_imports_without_name_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    repo_root = str(Path(__file__).resolve().parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    for mod in [m for m in list(sys.modules) if m == "app.main" or m.startswith("app.")]:
        sys.modules.pop(mod, None)

    module = importlib.import_module("app.main")
    assert hasattr(module, "app")
