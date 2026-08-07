"""Validate a Skycat release after it has been published to TestPyPI."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tomllib
from typing import TypeVar
import urllib.request
import venv


PROJECT = "skycat"
TESTPYPI_SIMPLE = "https://test.pypi.org/simple/"
TESTPYPI_SIMPLE_PROJECT = f"{TESTPYPI_SIMPLE}{PROJECT}/"
PYPI_SIMPLE = "https://pypi.org/simple/"
TESTPYPI_JSON_API = "https://test.pypi.org/pypi/skycat/{version}/json"

T = TypeVar("T")


def main() -> int:
    version = os.environ.get("RELEASE_VERSION")
    if not version:
        print("RELEASE_VERSION is required", file=sys.stderr)
        return 2

    dist_dir = Path(os.environ.get("DIST_DIR", "dist"))
    metadata = check_testpypi_metadata(version)
    check_simple_index(version)
    uploaded_files = check_uploaded_files(metadata, version, dist_dir)
    install_from_testpypi(version, wheel_url=uploaded_files[f"{PROJECT}-{version}-py3-none-any.whl"])
    return 0


def check_testpypi_metadata(version: str) -> dict[str, object]:
    metadata = retry(
        lambda: fetch_json(TESTPYPI_JSON_API.format(version=version)),
        label=f"TestPyPI JSON metadata for {PROJECT}=={version}",
    )
    info = require_mapping(metadata.get("info"), "info")
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert_equal("info.name", info.get("name"), PROJECT)
    assert_equal("info.version", info.get("version"), version)
    assert_equal("pyproject version", project["version"], version)
    assert_specifier_set_equal(
        "info.requires_python",
        require_str(info.get("requires_python"), "info.requires_python"),
        project["requires-python"],
    )
    assert_equal("info.license_expression", info.get("license_expression"), project["license"])
    assert_equal("info.description_content_type", info.get("description_content_type"), "text/markdown")

    author_names, author_emails = split_people(project.get("authors", []))
    maintainer_names, maintainer_emails = split_people(project.get("maintainers", []))
    if author_names:
        assert_equal("info.author", info.get("author"), ", ".join(author_names))
    if author_emails:
        assert_equal("info.author_email", info.get("author_email"), ", ".join(author_emails))
    if maintainer_names:
        assert_equal("info.maintainer", info.get("maintainer"), ", ".join(maintainer_names))
    if maintainer_emails:
        assert_equal("info.maintainer_email", info.get("maintainer_email"), ", ".join(maintainer_emails))

    actual_urls = require_mapping(info.get("project_urls"), "info.project_urls")
    mismatched_urls = {
        key: {"expected": value, "actual": actual_urls.get(key)}
        for key, value in project.get("urls", {}).items()
        if actual_urls.get(key) != value
    }
    if mismatched_urls:
        raise RuntimeError(f"TestPyPI project URL mismatch: {mismatched_urls}")

    check_readme_source(require_str(info.get("description"), "info.description"))
    print(f"validated TestPyPI JSON metadata for {PROJECT} {version}", flush=True)
    return metadata


def check_simple_index(version: str) -> None:
    html = retry(
        lambda: fetch(TESTPYPI_SIMPLE_PROJECT, accept="text/html"),
        label=f"TestPyPI Simple API for {PROJECT}",
    )
    missing = [filename for filename in expected_distribution_names(version) if filename not in html]
    if missing:
        raise RuntimeError(f"TestPyPI Simple API is missing release files: {missing}")
    print(f"validated TestPyPI Simple API files for {PROJECT} {version}", flush=True)


def check_uploaded_files(metadata: dict[str, object], version: str, dist_dir: Path) -> dict[str, str]:
    urls = metadata.get("urls")
    if not isinstance(urls, list):
        raise RuntimeError("TestPyPI JSON metadata did not contain a urls list")

    uploaded = {
        require_str(require_mapping(item, "urls[]").get("filename"), "urls[].filename"): require_mapping(
            item, "urls[]"
        )
        for item in urls
    }
    expected_types = {
        f"{PROJECT}-{version}-py3-none-any.whl": "bdist_wheel",
        f"{PROJECT}-{version}.tar.gz": "sdist",
    }

    missing = sorted(set(expected_types) - set(uploaded))
    if missing:
        raise RuntimeError(f"TestPyPI JSON metadata is missing release files: {missing}")

    for filename, expected_type in expected_types.items():
        file_metadata = uploaded[filename]
        assert_equal(f"{filename} packagetype", file_metadata.get("packagetype"), expected_type)
        url = require_str(file_metadata.get("url"), f"{filename}.url")
        if not url.startswith("https://test-files.pythonhosted.org/"):
            raise RuntimeError(f"{filename} does not point at TestPyPI file storage: {url}")

        local_file = dist_dir / filename
        if not local_file.is_file():
            raise RuntimeError(f"missing local release artifact for hash comparison: {local_file}")

        digests = require_mapping(file_metadata.get("digests"), f"{filename}.digests")
        assert_equal(
            f"{filename} sha256",
            digests.get("sha256"),
            sha256_file(local_file),
        )

    print(f"validated TestPyPI artifact hashes for {PROJECT} {version}", flush=True)
    return {
        filename: require_str(file_metadata.get("url"), f"{filename}.url")
        for filename, file_metadata in uploaded.items()
        if filename in expected_types
    }


def check_readme_source(description: str) -> None:
    required_fragments = [
        "https://raw.githubusercontent.com/SkynetRTN/skycat/main/brand/skycat_logo.png",
        "https://github.com/SkynetRTN/skycat/blob/main/docs/operations/release.md",
        "https://github.com/SkynetRTN/skycat/blob/main/docs/reference/architecture.md",
        "https://github.com/SkynetRTN/skycat/blob/main/LICENSE",
        "python -m pip install skycat",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in description]
    if missing:
        raise RuntimeError(f"missing expected README metadata content: {missing}")

    bad_fragments = [
        "](docs/",
        "](./docs/",
        "](LICENSE",
        "](./LICENSE",
        'src="brand/',
        'src="./brand/',
    ]
    found = [fragment for fragment in bad_fragments if fragment in description]
    if found:
        raise RuntimeError(f"project-relative README references remain: {found}")


def install_from_testpypi(version: str, *, wheel_url: str) -> None:
    with tempfile.TemporaryDirectory(prefix="skycat-testpypi-") as tmp:
        venv_dir = Path(tmp) / "venv"
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        python = venv_dir / "bin" / "python"
        skycat = venv_dir / "bin" / "skycat"

        run([python, "-m", "pip", "install", "--upgrade", "pip"])

        def install() -> None:
            run(
                [
                    python,
                    "-m",
                    "pip",
                    "install",
                    "--index-url",
                    PYPI_SIMPLE,
                    "--no-cache-dir",
                    wheel_url,
                ]
            )

        retry(install, label=f"install {PROJECT}=={version} from TestPyPI wheel")

        run([skycat, "--help"])
        run([skycat, "config"])
        run(
            [
                python,
                "-c",
                (
                    "import importlib.metadata as m, os, skycat; "
                    "from alembic.script import ScriptDirectory; "
                    "from skycat.config import CatalogDatabaseConfig; "
                    "from skycat.database.migrate import make_alembic_config; "
                    "expected=os.environ['RELEASE_VERSION']; "
                    "assert skycat.__version__ == expected, (skycat.__version__, expected); "
                    "assert m.version('skycat') == expected, (m.version('skycat'), expected); "
                    "script=ScriptDirectory.from_config(make_alembic_config(CatalogDatabaseConfig())); "
                    "heads=script.get_heads(); revisions=list(script.walk_revisions()); "
                    "assert len(heads) == 1, heads; "
                    "assert revisions, 'no packaged migrations'; "
                    "print(f'skycat {skycat.__version__}; packaged migrations: {len(revisions)} revisions, head {heads[0]}')"
                ),
            ],
            env={**os.environ, "RELEASE_VERSION": version},
        )
        print(f"validated TestPyPI install for {PROJECT} {version}", flush=True)


def expected_distribution_names(version: str) -> list[str]:
    return [f"{PROJECT}-{version}-py3-none-any.whl", f"{PROJECT}-{version}.tar.gz"]


def fetch_json(url: str) -> dict[str, object]:
    raw = json.loads(fetch(url, accept="application/json"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"expected JSON object from {url}")
    return raw


def fetch(url: str, *, accept: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "skycat-release-check/1",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def retry(func: Callable[[], T], *, label: str, attempts: int = 18, delay_s: int = 10) -> T:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - this is a release propagation retry.
            last_error = exc
            if attempt == attempts:
                break
            print(f"{label} not ready yet ({attempt}/{attempts}): {exc}", file=sys.stderr, flush=True)
            time.sleep(delay_s)
    raise RuntimeError(f"{label} failed after {attempts} attempts") from last_error


def require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def require_str(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"{label} must be a string")
    return value


def assert_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise RuntimeError(f"{label} mismatch: expected {expected!r}, got {actual!r}")


def assert_specifier_set_equal(label: str, actual: str, expected: str) -> None:
    if split_specifier_set(actual) != split_specifier_set(expected):
        raise RuntimeError(f"{label} mismatch: expected {expected!r}, got {actual!r}")


def split_specifier_set(value: str) -> list[str]:
    return sorted(part.strip() for part in value.split(",") if part.strip())


def split_people(people: object) -> tuple[list[str], list[str]]:
    if not isinstance(people, list):
        raise RuntimeError("project people metadata must be a list")

    names: list[str] = []
    emails: list[str] = []
    for person in people:
        data = require_mapping(person, "project people metadata item")
        name = data.get("name")
        email = data.get("email")
        if isinstance(name, str) and isinstance(email, str):
            emails.append(f"{name} <{email}>")
        elif isinstance(email, str):
            emails.append(email)
        elif isinstance(name, str):
            names.append(name)
    return names, emails


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    args: list[str | Path],
    *,
    env: dict[str, str] | None = None,
) -> None:
    printable = " ".join(str(arg) for arg in args)
    print(f"+ {printable}", flush=True)
    subprocess.run([str(arg) for arg in args], check=True, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
