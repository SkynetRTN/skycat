# Changelog

Skycat uses GitHub Releases as the release-notes surface. This file records the
source-controlled summary that release notes should start from.

## Unreleased

## 0.1.4 - 2026-08-08

- Automated TestPyPI publishing and validation on release tag pushes.
- Added a TestPyPI install and rendered-page preflight before manual PyPI
  publishing.

## 0.1.3 - 2026-08-08

- Fixed PyPI/TestPyPI README documentation links by using absolute GitHub URLs.
- Updated package-index installation notes to install the latest published
  release without a version pin.

## 0.1.2 - 2026-08-07

- Fixed PyPI/TestPyPI README logo rendering by using an absolute hosted logo URL.
- Corrected PyPI author and maintainer metadata.
- Condensed the README into a shorter package landing page that points to the
  detailed docs.

## 0.1.1 - 2026-08-07

- Added a manual Trusted Publishing lane for TestPyPI/PyPI release uploads.
- Added Twine metadata checks to package build and release validation.

## 0.1.0 - 2026-08-07

- Prepared GitHub-hosted package release documentation and metadata.
- Declared GPLv3 package license metadata.
- Added CI coverage for package version drift.
