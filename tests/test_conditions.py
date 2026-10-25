"""Tests for the road condition ranking."""

from __future__ import annotations

import pytest

from custom_components.ontario_511.conditions import RoadCondition, classify, worst
from custom_components.ontario_511.models import FerryStatus


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("Bare and dry road", RoadCondition.GOOD, id="seen_dry"),
        pytest.param("Bare and wet road", RoadCondition.WET, id="seen_wet"),
        pytest.param("No Report", RoadCondition.UNKNOWN, id="seen_no_report"),
        pytest.param("Partly snow covered", RoadCondition.PARTLY_COVERED, id="partly"),
        pytest.param("Snow packed", RoadCondition.COVERED, id="snow_packed"),
        pytest.param("snow covered", RoadCondition.COVERED, id="documented_snow"),
        pytest.param("Ice covered", RoadCondition.ICY, id="ice"),
        pytest.param("Road closed", RoadCondition.CLOSED, id="closed"),
        pytest.param("Something new", RoadCondition.UNKNOWN, id="unrecognised"),
        pytest.param("", RoadCondition.UNKNOWN, id="empty"),
        pytest.param(None, RoadCondition.UNKNOWN, id="none"),
    ],
)
def test_classify(text: str | None, expected: RoadCondition) -> None:
    """Text maps to a condition; anything unrecognised is unknown, not good."""
    assert classify(text) is expected


async def test_unrecognised_condition_is_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A new condition name is reported once so it can be added later."""
    classify("Glitter covered")
    classify("Glitter covered")
    assert caplog.text.count("Glitter covered") == 1


def test_worst_and_hazard() -> None:
    """The worst condition wins; unknown only wins when nothing is known."""
    assert worst(["Bare and dry road", "Partly snow covered"]) is (
        RoadCondition.PARTLY_COVERED
    )
    assert worst(["No Report", "Bare and dry road"]) is RoadCondition.GOOD
    assert worst([]) is RoadCondition.UNKNOWN
    assert not RoadCondition.WET.is_hazardous
    assert RoadCondition.PARTLY_COVERED.is_hazardous
    assert not RoadCondition.UNKNOWN.is_hazardous


def test_no_report_is_home_assistants_unknown() -> None:
    """ "No Report" has no value (None), which Home Assistant shows as Unknown.

    Home Assistant skips the enum option check for None, so the options list
    only holds real conditions and statuses.
    """
    assert classify("No Report").option is None
    assert RoadCondition.options() == [
        "good",
        "wet",
        "partly_covered",
        "covered",
        "icy",
        "closed",
    ]
    assert FerryStatus.parse("No Status Available") is None
    assert FerryStatus.parse("Not In Service") == "not_in_service"
