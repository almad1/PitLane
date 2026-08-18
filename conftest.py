"""Pytest bootstrap.

Puts collector/ and dashboard/ on sys.path (each is a flat script directory,
not a package), points the dashboard's writable files at a temp dir so tests
never touch /data, and chdirs to dashboard/ so its relative `static` mount
resolves when API tests request pages.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))

# dashboard first: `import main` in tests means dashboard/main.py.
sys.path.insert(0, os.path.join(ROOT, "collector"))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

_tmp = tempfile.mkdtemp(prefix="pitlane-tests-")
os.environ.setdefault("LAYOUT_FILE", os.path.join(_tmp, "layout.json"))
os.environ.setdefault("GROUPS_FILE", os.path.join(_tmp, "groups.json"))
os.environ.setdefault("RELAY_FILE", os.path.join(_tmp, "latest.json"))

os.chdir(os.path.join(ROOT, "dashboard"))
