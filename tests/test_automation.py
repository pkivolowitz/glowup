"""Tests for :func:`automation.validate_automation`.

Only the schema-validation helper survives in ``automation.py`` —
trigger evaluation moved to the operator framework
(``operators/trigger.py`` and Chapter 31).  Pre-2026-04 tests for
the deleted ``AutomationManager`` / ``SensorData`` /
``_AutomationState`` machinery were removed alongside the code
they exercised.
"""

# Copyright (c) 2026 Perry Kivolowitz. All rights reserved.
# Licensed under the MIT License. See LICENSE file in the project root.

__version__: str = "2.0"

import unittest
from typing import Any, Optional

from automation import (
    DEFAULT_WATCHDOG_MINUTES,
    VALID_CHARACTERISTICS,
    VALID_CONFLICT_POLICIES,
    _CONDITION_OPS,
    validate_automation,
)


# ---------------------------------------------------------------------------
# Test data factory
# ---------------------------------------------------------------------------


def _make_automation(
    name: str = "test",
    label: str = "sensor1",
    characteristic: str = "motion",
    condition: str = "eq",
    value: Any = 1,
    group: str = "Living Room",
    effect: str = "on",
    params: Optional[dict] = None,
    off_type: str = "watchdog",
    off_minutes: float = 30.0,
    enabled: bool = True,
    policy: str = "defer",
) -> dict[str, Any]:
    """Build a valid automation entry for testing."""
    return {
        "name": name,
        "enabled": enabled,
        "sensor": {
            "type": "matter",
            "label": label,
            "characteristic": characteristic,
        },
        "trigger": {"condition": condition, "value": value},
        "action": {
            "group": group,
            "effect": effect,
            "params": params or {"brightness": 70},
        },
        "off_trigger": {"type": off_type, "minutes": off_minutes},
        "off_action": {"effect": "off", "params": {}},
        "schedule_conflict": policy,
    }


# ---------------------------------------------------------------------------
# validate_automation
# ---------------------------------------------------------------------------


class TestValidateAutomation(unittest.TestCase):
    """Tests for :func:`automation.validate_automation`."""

    KNOWN_GROUPS: set[str] = {"Living Room", "Bedroom"}
    KNOWN_EFFECTS: set[str] = {
        "on", "off", "breathe", "cylon", "spectrum2d", "soundlevel",
    }
    MEDIA_EFFECTS: set[str] = {"spectrum2d", "soundlevel"}

    def _validate(self, entry: dict) -> list[str]:
        return validate_automation(
            entry,
            self.KNOWN_GROUPS,
            self.KNOWN_EFFECTS,
            self.MEDIA_EFFECTS,
        )

    def test_valid_entry(self) -> None:
        """A well-formed entry returns no errors."""
        self.assertEqual(self._validate(_make_automation()), [])

    def test_missing_name(self) -> None:
        entry = _make_automation()
        del entry["name"]
        errors = self._validate(entry)
        self.assertTrue(any("name" in e.lower() for e in errors))

    def test_missing_sensor_label(self) -> None:
        entry = _make_automation()
        del entry["sensor"]["label"]
        errors = self._validate(entry)
        self.assertTrue(any("sensor.label" in e for e in errors))

    def test_invalid_characteristic(self) -> None:
        errors = self._validate(_make_automation(characteristic="bogus"))
        self.assertTrue(any("characteristic" in e for e in errors))

    def test_invalid_condition(self) -> None:
        errors = self._validate(_make_automation(condition="banana"))
        self.assertTrue(any("condition" in e for e in errors))

    def test_missing_trigger_value(self) -> None:
        entry = _make_automation()
        del entry["trigger"]["value"]
        errors = self._validate(entry)
        self.assertTrue(any("trigger.value" in e for e in errors))

    def test_unknown_group(self) -> None:
        errors = self._validate(_make_automation(group="Nonexistent"))
        self.assertTrue(any("Unknown group" in e for e in errors))

    def test_unknown_effect(self) -> None:
        errors = self._validate(_make_automation(effect="nope"))
        self.assertTrue(any("Unknown effect" in e for e in errors))

    def test_media_effect_rejected(self) -> None:
        """Media/audio effects are not allowed in automations."""
        errors = self._validate(_make_automation(effect="spectrum2d"))
        self.assertTrue(
            any("media" in e.lower() or "audio" in e.lower() for e in errors)
        )

    def test_invalid_watchdog_minutes(self) -> None:
        entry = _make_automation()
        entry["off_trigger"]["minutes"] = -5
        errors = self._validate(entry)
        self.assertTrue(any("minutes" in e for e in errors))

    def test_invalid_off_trigger_type(self) -> None:
        entry = _make_automation()
        entry["off_trigger"]["type"] = "bogus"
        errors = self._validate(entry)
        self.assertTrue(any("off_trigger.type" in e for e in errors))

    def test_invalid_conflict_policy(self) -> None:
        errors = self._validate(_make_automation(policy="bogus"))
        self.assertTrue(any("schedule_conflict" in e for e in errors))

    def test_garbage_sensor_type(self) -> None:
        """Non-dict sensor field doesn't crash."""
        entry = _make_automation()
        entry["sensor"] = 42
        self.assertTrue(len(self._validate(entry)) > 0)

    def test_garbage_trigger_type(self) -> None:
        """Non-dict trigger field doesn't crash."""
        entry = _make_automation()
        entry["trigger"] = "not a dict"
        self.assertTrue(len(self._validate(entry)) > 0)

    def test_garbage_conflict_policy_type(self) -> None:
        """Non-string conflict policy (e.g., list) doesn't crash."""
        entry = _make_automation()
        entry["schedule_conflict"] = [1, 2, 3]
        errors = self._validate(entry)
        self.assertTrue(any("schedule_conflict" in e for e in errors))

    def test_all_valid_characteristics(self) -> None:
        """Every known characteristic passes validation."""
        for char in VALID_CHARACTERISTICS:
            errors = self._validate(_make_automation(characteristic=char))
            self.assertEqual(
                errors, [], f"Characteristic {char!r} failed",
            )

    def test_all_valid_conditions(self) -> None:
        """Every known condition operator passes validation."""
        for op_name in _CONDITION_OPS:
            errors = self._validate(_make_automation(condition=op_name))
            self.assertEqual(
                errors, [], f"Condition {op_name!r} failed",
            )

    def test_all_valid_policies(self) -> None:
        """Every known conflict policy passes validation."""
        for policy in VALID_CONFLICT_POLICIES:
            errors = self._validate(_make_automation(policy=policy))
            self.assertEqual(
                errors, [], f"Policy {policy!r} failed",
            )

    def test_default_watchdog_minutes_constant_present(self) -> None:
        """``DEFAULT_WATCHDOG_MINUTES`` is exported and positive.

        ``validate_automation`` uses it as the default when an
        entry omits ``off_trigger.minutes``.  If the constant is
        ever deleted or set to a non-positive value, every entry
        without an explicit watchdog minutes will start failing
        validation silently.
        """
        self.assertIsInstance(DEFAULT_WATCHDOG_MINUTES, (int, float))
        self.assertGreater(DEFAULT_WATCHDOG_MINUTES, 0)


if __name__ == "__main__":
    unittest.main()
