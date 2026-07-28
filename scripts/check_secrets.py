import json
import subprocess

scan = subprocess.run(
    [
        "detect-secrets",
        "scan",
        "--all-files",
        "--exclude-files",
        r"(^\.(cache|git|idea|mypy_cache|pytest_cache|ruff_cache|uv-cache|venv)/|^frontend/(build|node_modules)/|^uv\.lock$|^frontend/package-lock\.json$|^openapi\.json$)",
    ],
    check=True,
    capture_output=True,
    text=True,
)
findings = json.loads(scan.stdout)["results"]
if findings:
    for path, entries in findings.items():
        for entry in entries:
            print(f"{path}:{entry['line_number']}: {entry['type']}")
    raise SystemExit(1)
