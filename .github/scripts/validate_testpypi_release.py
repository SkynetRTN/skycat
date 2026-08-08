"""Validate a Skycat release after it has been published to TestPyPI."""

from __future__ import annotations

from html.parser import HTMLParser
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import venv


PROJECT = "skycat"
TESTPYPI_SIMPLE = "https://test.pypi.org/simple/"
PYPI_SIMPLE = "https://pypi.org/simple/"
TESTPYPI_PROJECT_PAGE = "https://test.pypi.org/project/skycat/{version}/"


class ReadmeLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.srcs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is None:
                continue
            if tag == "a" and name == "href":
                self.hrefs.append(value)
            elif tag == "img" and name == "src":
                self.srcs.append(value)


def main() -> int:
    version = os.environ.get("RELEASE_VERSION")
    if not version:
        print("RELEASE_VERSION is required", file=sys.stderr)
        return 2

    install_from_testpypi(version)
    check_testpypi_page(version)
    return 0


def install_from_testpypi(version: str) -> None:
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
                    TESTPYPI_SIMPLE,
                    "--extra-index-url",
                    PYPI_SIMPLE,
                    f"{PROJECT}=={version}",
                ]
            )

        retry(install, label=f"install {PROJECT}=={version} from TestPyPI")

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


def check_testpypi_page(version: str) -> None:
    page_url = TESTPYPI_PROJECT_PAGE.format(version=version)

    def fetch_and_check() -> None:
        html = fetch(page_url)
        parser = ReadmeLinkParser()
        parser.feed(html)

        bad_refs = [
            value
            for value in [*parser.hrefs, *parser.srcs]
            if value.startswith(("docs/", "LICENSE"))
            or "/project/skycat/docs/" in value
            or "/project/skycat/LICENSE" in value
        ]
        if bad_refs:
            raise RuntimeError(f"project-relative README links remain: {bad_refs}")

        required_fragments = [
            "https://raw.githubusercontent.com/SkynetRTN/skycat/main/brand/skycat_logo.png",
            "https://github.com/SkynetRTN/skycat/blob/main/docs/operations/release.md",
            "https://github.com/SkynetRTN/skycat/blob/main/docs/reference/architecture.md",
            "https://github.com/SkynetRTN/skycat/blob/main/LICENSE",
            "python -m pip install skycat",
        ]
        missing = [fragment for fragment in required_fragments if fragment not in html]
        if missing:
            raise RuntimeError(f"missing expected TestPyPI page content: {missing}")

        print(f"validated rendered TestPyPI page: {page_url}")

    retry(fetch_and_check, label=f"rendered TestPyPI page for {version}")


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "skycat-release-check/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def retry(func, *, label: str, attempts: int = 18, delay_s: int = 10) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            func()
            return
        except Exception as exc:  # noqa: BLE001 - this is a release propagation retry.
            last_error = exc
            if attempt == attempts:
                break
            print(f"{label} not ready yet ({attempt}/{attempts}): {exc}", file=sys.stderr)
            time.sleep(delay_s)
    raise RuntimeError(f"{label} failed after {attempts} attempts") from last_error


def run(
    args: list[str | Path],
    *,
    env: dict[str, str] | None = None,
) -> None:
    printable = " ".join(str(arg) for arg in args)
    print(f"+ {printable}")
    subprocess.run([str(arg) for arg in args], check=True, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
