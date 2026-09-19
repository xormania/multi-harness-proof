"""Process-level regression coverage for plausible failures, with no model calls."""
from pathlib import Path
import tempfile
import unittest

from behavior import SCENARIOS, run_scenario


class BehaviorTests(unittest.TestCase):
    pass


def scenario_test(name):
    def test(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_scenario(Path(directory) / name, name)
            self.assertEqual(result["status"], "matched", result)
    test.__doc__ = SCENARIOS[name][2]
    return test


for scenario in SCENARIOS:
    setattr(BehaviorTests, "test_" + scenario.replace("-", "_"), scenario_test(scenario))


if __name__ == "__main__":
    unittest.main()
