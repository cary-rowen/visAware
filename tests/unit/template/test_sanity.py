# Copyright (C) 2026 NV Access Limited, Abdel
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Sanity test suite for verifying unit test execution in the add-on template."""

import unittest


class TestTemplateSanity(unittest.TestCase):
	"""Minimal sanity test suite to verify the unit test runner setup."""

	def test_runner_handles_passing_tests(self):
		"""Ensure that the test runner correctly detects passing tests."""
		self.assertTrue(True)

	@unittest.expectedFailure
	def test_runner_handles_failing_tests(self):
		"""Ensure that the test runner correctly detects failing tests.

		Marked with @expectedFailure so CI remains green while demonstrating
		failure detection.
		"""
		self.assertTrue(False)
