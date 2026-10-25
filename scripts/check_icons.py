"""Check that every icon in icons.json exists in Material Design Icons.

Usage: uv run --group run python -m scripts.check_icons

hassfest doesn't check icon names, and a misspelled or missing name shows no
icon at all. This checks them against the icon list bundled with the Home
Assistant frontend (installed by the "run" dependency group).
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

import hass_frontend

ICONS = Path("custom_components/ontario_511/icons.json")


def main() -> None:
    """Report icons that don't exist."""
    icon_list = Path(hass_frontend.__file__).parent / "static" / "mdi" / "iconList.json"
    known = {
        entry["name"] if isinstance(entry, dict) else entry
        for entry in json.loads(icon_list.read_text(encoding="utf-8"))
    }
    used = set(re.findall(r'"mdi:([a-z0-9-]+)"', ICONS.read_text(encoding="utf-8")))
    if missing := sorted(used - known):
        sys.exit(f"Icons that don't exist: {', '.join(missing)}")
    print(f"All {len(used)} icons exist.")


if __name__ == "__main__":
    main()
