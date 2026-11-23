"""Run test modules in isolated Python processes.

Each legacy test module configures EDUBRIDGE_DB at import time. Separate processes
prevent their temporary database paths and imported module state from colliding.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("tests/test_services.py", "tests/test_auth.py", "tests/test_auth_ui.py")
failed = []
for module in MODULES:
    print(f"\\n=== {module} ===", flush=True)
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", module], cwd=ROOT)
    if result.returncode:
        failed.append(module)
if failed:
    print("\\nFailed:", ", ".join(failed))
    raise SystemExit(1)
print("\\nAll EduBridge-AI test modules passed.")
