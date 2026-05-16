"""Tests for polymorphic group membership.

Covers:

- ``server_utils.split_group_members`` — transport-prefix partition.
"""

# Copyright (c) 2026 Perry Kivolowitz. All rights reserved.
# Licensed under the MIT License. See LICENSE file in the project root.

__version__: str = "0.1"

import unittest

from server_utils import split_group_members


# ---------------------------------------------------------------------------
# split_group_members
# ---------------------------------------------------------------------------

class SplitGroupMembersTests(unittest.TestCase):
    """Transport-prefix partition of a group member list."""

    def test_lifx_only(self) -> None:
        lifx, matter = split_group_members(
            ["192.0.2.1", "Bedroom Lamp"])
        self.assertEqual(lifx, ["192.0.2.1", "Bedroom Lamp"])
        self.assertEqual(matter, [])

    def test_mixed(self) -> None:
        lifx, matter = split_group_members([
            "192.0.2.1",
            "matter:Kitchen Lamp",
            "192.0.2.2",
        ])
        self.assertEqual(lifx, ["192.0.2.1", "192.0.2.2"])
        self.assertEqual(matter, ["Kitchen Lamp"])

    def test_prefix_strip_preserves_name(self) -> None:
        """Prefix strip must not trim extra characters from the name."""
        _lifx, matter = split_group_members(["matter:lamp:foo"])
        # The entry is a Matter device whose name happens to contain
        # a colon; strip only the leading "matter:" prefix.
        self.assertEqual(matter, ["lamp:foo"])

    def test_empty(self) -> None:
        lifx, matter = split_group_members([])
        self.assertEqual((lifx, matter), ([], []))


if __name__ == "__main__":
    unittest.main()
