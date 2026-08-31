"""Release-engineering gates for a reproducible and scanned CI environment."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON_VERSION = "3.12.4"


def test_python_and_complete_test_toolchain_are_pinned() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dev = set(config["project"]["optional-dependencies"]["dev"])
    assert {
        "playwright==1.60.0",
        "pytest==9.0.3",
        "ruff==0.15.14",
        "setuptools==83.0.0",
    } <= dev
    assert config["build-system"]["requires"] == ["setuptools==83.0.0"]
    assert (ROOT / ".python-version").read_text().strip() == PYTHON_VERSION

    locked = set((ROOT / "requirements.lock").read_text().splitlines())
    assert {
        "playwright==1.60.0",
        "pytest==9.0.3",
        "ruff==0.15.14",
        "setuptools==83.0.0",
    } <= locked


def test_workflows_use_the_pinned_python_and_lock() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    monitor = (ROOT / ".github/workflows/source-monitor.yml").read_text()
    smoke = (ROOT / ".github/workflows/smoke.yml").read_text()
    for workflow in (ci, monitor, smoke):
        assert f'python-version: "{PYTHON_VERSION}"' in workflow

    assert "python -m pip install --requirement requirements.lock" in ci
    assert 'python -m pip install --no-deps -e "."' in ci
    assert "python -m pip check" in ci
    assert "python -m pip install --requirement requirements.lock" in monitor
    assert 'python -m pip install --no-deps -e "."' in monitor
    assert "python -m pip install --requirement requirements.lock" in smoke
    assert "python -m pip check" in smoke


def test_browser_gate_cannot_self_skip_missing_dependencies() -> None:
    browser_tests = [
        *sorted((ROOT / "tests").glob("test_browser*.py")),
        ROOT / "tests/test_render_blocks.py",
    ]
    for path in browser_tests:
        source = path.read_text()
        assert 'importorskip("playwright"' not in source, path.name
        assert "pytest.skip" not in source, path.name


def test_security_workflow_scans_secrets_and_dependencies() -> None:
    security = (ROOT / ".github/workflows/security.yml").read_text()
    # The binary is pinned by version and checked by digest, so a moved release
    # tag cannot change what runs.
    assert "GITLEAKS_VERSION: 8.30.0" in security
    assert re.search(r"GITLEAKS_SHA256: [0-9a-f]{64}", security)
    assert "sha256sum --check --strict" in security
    assert "gitleaks git --config .gitleaks.toml" in security
    assert "gitleaks dir --config .gitleaks.toml" in security
    assert re.search(r"astral-sh/setup-uv@[0-9a-f]{40}", security)
    assert "pip-audit==2.10.1 --strict" in security
    assert "--path /tmp/audit-env/lib/python3.12/site-packages" in security
    assert re.search(r"actions/setup-node@[0-9a-f]{40}", security)
    assert 'node-version: "24.20.0"' in security
    assert "npm ci" in security
    assert "npm audit --audit-level=moderate" in security
    assert "npm run build" in security
    assert "git diff --exit-code -- public/vendor/echarts-custom-6.1.0-r1.min.js" in security
    assert (ROOT / ".gitleaks.toml").exists()


def test_echarts_vendor_build_is_committed_and_exactly_pinned() -> None:
    build_dir = ROOT / "vendor/echarts-build"
    package = json.loads((build_dir / "package.json").read_text())
    assert package["devDependencies"] == {
        "echarts": "6.1.0",
        "esbuild": "0.28.2",
    }
    assert package["scripts"]["build"].endswith(
        "--outfile=../../public/vendor/echarts-custom-6.1.0-r1.min.js"
    )
    assert (build_dir / "package-lock.json").exists()
    assert (build_dir / "entry.js").exists()
    assert "node_modules/" in (ROOT / ".gitignore").read_text().splitlines()
    bundle = ROOT / "public/vendor/echarts-custom-6.1.0-r1.min.js"
    assert bundle.exists()
    index = (ROOT / "public/index.html").read_text()
    assert 'src="vendor/echarts-custom-6.1.0-r1.min.js"' in index
    assert re.search(
        r'src="vendor/echarts-custom-6\.1\.0-r1\.min\.js"\s+'
        r'integrity="sha384-[A-Za-z0-9+/]{64}"',
        index,
    )
    sri = base64.b64encode(hashlib.sha384(bundle.read_bytes()).digest()).decode()
    assert f'integrity="sha384-{sri}"' in index
    readme = (ROOT / "public/vendor/README.md").read_text()
    assert "tmp/echarts-build" not in readme
    assert "npm ci" in readme


def _tracked(*paths: str) -> None:
    import subprocess

    for path in paths:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", path],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{path} is not tracked by git, so a release can omit it"


def test_every_load_bearing_release_file_is_tracked_by_git() -> None:
    """Existence on disk is not shipping. Only a tracked file reaches Vercel.

    ``public/index.html`` loads the vendor bundle by name. An untracked bundle
    deploys a page with no chart, and the reproducibility gate in security.yml
    reads ``git diff``, which reports nothing at all for an untracked file.
    """
    _tracked(
        "public/vendor/echarts-custom-6.1.0-r1.min.js",
        "vendor/echarts-build/entry.js",
        "vendor/echarts-build/package.json",
        "vendor/echarts-build/package-lock.json",
        "vendor/echarts-build/README.md",
        ".github/workflows/security.yml",
        ".gitleaks.toml",
        ".python-version",
        "tests/test_release_contract.py",
        "requirements.lock",
    )


def test_the_old_vulnerable_bundle_is_gone_from_the_tree() -> None:
    import subprocess

    listed = subprocess.run(
        ["git", "ls-files", "--", "public/vendor/"], cwd=ROOT, capture_output=True, text=True
    ).stdout
    assert "5.6.0" not in listed, "the superseded ECharts bundle is still tracked"
    assert not (ROOT / "public/vendor/echarts-custom-5.6.0-r2.min.js").exists()


def test_gitleaks_allowlist_cannot_hide_a_secret_under_a_json_filename_key() -> None:
    """A digest allowlist must be scoped, never a wildcard over every .json key."""
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text())
    for allowlist in config["allowlists"]:
        wildcard = any('[^"]+\\.(?:json|geojson)' in r for r in allowlist["regexes"])
        assert not wildcard, "the allowlist matches any .json key, so it can hide a real token"
        if allowlist["regexes"] == ['"[0-9a-f]{64}"']:
            assert allowlist["paths"], "a bare digest pattern must be scoped to generated files"
            assert all(p.startswith("^") and p.endswith("$") for p in allowlist["paths"])
