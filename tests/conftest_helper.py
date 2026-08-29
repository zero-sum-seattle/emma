"""Shared helper for loading the extensionless `emma` script as a module.

`emma` has no .py suffix (it's meant to be run directly, not imported), so
tests can't just `import emma`. Each test file that needs it should do:

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest_helper import load_emma

    emma = load_emma()
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

EMMA_PATH = Path(__file__).resolve().parent.parent / "emma"


def load_emma():
    """Load a fresh copy of the emma module, bypassing sys.modules caching.

    Each call re-executes the module from source so tests that need
    different environment variables (which some module-level constants
    read at import time) don't see a stale cached instance.
    """
    loader = SourceFileLoader("emma", str(EMMA_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module
