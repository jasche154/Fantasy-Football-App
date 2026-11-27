"""M2: every simulator module imports on its own (an import cycle only shows when a module is imported first)."""
import subprocess
import sys
from pathlib import Path

SERVICES = Path(__file__).resolve().parent.parent / "services"
SCRIPT = """
import importlib, sys
failed = []
for name in sys.argv[1:]:
    for loaded in [m for m in sys.modules if m == "services" or m.startswith("services.")]:
        del sys.modules[loaded]                      # each module first, as if alone
    try:
        importlib.import_module("services." + name)
    except Exception as exc:
        failed.append(f"{name}: {type(exc).__name__}: {exc}")
print(*failed, sep="; ")
sys.exit(1 if failed else 0)
"""


def test_each_sim_module_imports_first_and_alone():
    modules = sorted(p.stem for p in SERVICES.glob("sim_*.py"))
    done = subprocess.run([sys.executable, "-c", SCRIPT, *modules], cwd=SERVICES.parent, capture_output=True,
                          text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
