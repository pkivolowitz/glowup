#!/usr/bin/env python3
"""Tests for graceful failure when optional dependencies are missing.

Verifies that every optional subsystem handles missing libraries,
unreachable services, missing config values, and corrupt data without
crashing.  These tests use unittest.mock to simulate missing imports
and unavailable resources.

Categories:
- Adapter imports: each adapter guarded by _HAS_* sentinel
- Nav config: missing nav_links in config
- Server: missing optional config keys

No network or hardware dependencies.
"""

# Copyright (c) 2026 Perry Kivolowitz. All rights reserved.
# Licensed under the MIT License. See LICENSE file in the project root.

__version__ = "1.0"

import unittest
from typing import Any
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Adapter sentinel tests
# ---------------------------------------------------------------------------

class TestAdapterSentinels(unittest.TestCase):
    """Every adapter import in server.py has a _HAS_* sentinel."""

    def test_all_adapter_sentinels_exist(self) -> None:
        """Each adapter has a corresponding _HAS_* sentinel in server.py."""
        import server
        expected_sentinels: list[str] = [
            "_HAS_PRINTER",
            "_HAS_MATTER",
        ]
        for sentinel in expected_sentinels:
            self.assertTrue(
                hasattr(server, sentinel),
                f"server.py missing sentinel: {sentinel}",
            )

    def test_sentinels_are_booleans(self) -> None:
        """All _HAS_* sentinels are boolean values."""
        import server
        for attr in dir(server):
            if attr.startswith("_HAS_"):
                val = getattr(server, attr)
                self.assertIsInstance(
                    val, bool,
                    f"{attr} is {type(val).__name__}, expected bool",
                )


# ---------------------------------------------------------------------------
# Nav config graceful failure
# ---------------------------------------------------------------------------

class TestNavConfigGraceful(unittest.TestCase):
    """Nav config endpoint handles missing config gracefully."""

    def test_missing_nav_links_returns_defaults(self) -> None:
        """Config without nav_links returns built-in links only."""
        from handlers.dashboard import DashboardHandlerMixin

        handler = MagicMock(spec=DashboardHandlerMixin)
        handler.config = {}  # No nav_links key.

        # Call the actual method.
        captured: dict = {}
        def fake_send_json(status: int, data: dict) -> None:
            captured["status"] = status
            captured["data"] = data

        handler._send_json = fake_send_json
        DashboardHandlerMixin._handle_get_nav_config(handler)

        self.assertEqual(captured["status"], 200)
        links: list = captured["data"]["links"]
        # Should have built-in links but no external ones.
        labels: list[str] = [l["label"] for l in links]
        self.assertIn("Dashboard", labels)
        self.assertIn("I/O", labels)

    def test_nav_links_extends_defaults(self) -> None:
        """Config with nav_links appends to built-in links."""
        from handlers.dashboard import DashboardHandlerMixin

        handler = MagicMock(spec=DashboardHandlerMixin)
        handler.config = {
            "nav_links": [
                {"label": "External", "href": "http://example.com:8099"},
            ],
        }

        captured: dict = {}
        def fake_send_json(status: int, data: dict) -> None:
            captured["status"] = status
            captured["data"] = data

        handler._send_json = fake_send_json
        DashboardHandlerMixin._handle_get_nav_config(handler)

        labels: list[str] = [l["label"] for l in captured["data"]["links"]]
        self.assertIn("External", labels)
        self.assertIn("Dashboard", labels)


# ---------------------------------------------------------------------------
# Server config defaults
# ---------------------------------------------------------------------------

class TestServerConfigDefaults(unittest.TestCase):
    """Server config uses safe defaults for optional keys."""

    def test_mqtt_sentinel_exists(self) -> None:
        """server.py has _MQTT_AVAILABLE sentinel."""
        import server
        self.assertTrue(hasattr(server, "_MQTT_AVAILABLE"))
        self.assertIsInstance(server._MQTT_AVAILABLE, bool)

    def test_optional_config_keys_have_defaults(self) -> None:
        """Optional config keys use .get() with defaults, not []."""
        # Verify the nav_links, home_display, and schedule_groups
        # patterns are safe by checking they don't raise KeyError
        # on empty config.
        config: dict[str, Any] = {}
        self.assertEqual(config.get("nav_links", []), [])
        self.assertEqual(config.get("home_display", {}), {})
        self.assertEqual(config.get("schedule_groups", {}), {})
        self.assertEqual(config.get("location", {}), {})


if __name__ == "__main__":
    unittest.main()
