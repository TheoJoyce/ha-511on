"""Sanity checks for the integration manifest and version."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

from awesomeversion import AwesomeVersion, AwesomeVersionStrategy
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from custom_components.ontario_511.const import DOMAIN

ROOT = Path(__file__).parent.parent
MANIFEST = ROOT / "custom_components" / DOMAIN / "manifest.json"


async def test_integration_loads(hass: HomeAssistant) -> None:
    """The custom integration is discoverable under its domain."""
    integration = await async_get_integration(hass, DOMAIN)
    assert integration.domain == DOMAIN


def test_versions_match_and_are_semver() -> None:
    """manifest.json and pyproject.toml carry the same SemVer version."""
    manifest_version = json.loads(MANIFEST.read_text())["version"]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert manifest_version == project["version"]
    assert AwesomeVersion(manifest_version).strategy is AwesomeVersionStrategy.SEMVER


def test_changelog_has_the_version() -> None:
    """Merging into main publishes the release with this section as its notes."""
    version = json.loads(MANIFEST.read_text())["version"]
    sections = re.split(
        r"^## ", (ROOT / "CHANGELOG.md").read_text(), flags=re.MULTILINE
    )
    (section,) = [s for s in sections if s.startswith(f"[{version}]")]
    assert section.split("\n", 1)[1].strip()
