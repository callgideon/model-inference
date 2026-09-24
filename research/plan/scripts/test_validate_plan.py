"""Negative checks for the launch plan's coverage and gate safeguards."""
import json
import unittest

from validate_plan import PLAN, consumer_closure_errors


class ConsumerClosureTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((PLAN / 'tasks.json').read_text())
        self.tasks = {t['id']: t for t in self.manifest['tasks']}

    def errors(self):
        return consumer_closure_errors(self.manifest, self.tasks)

    def test_current_graph(self):
        self.assertEqual(self.errors(), [])

    def test_missing_finding_is_rejected(self):
        del self.manifest['consumer_v1_closure']['finding_tasks']['RV-03']
        self.assertTrue(any('exactly RV' in e for e in self.errors()))

    def test_old_certificate_cannot_bypass_repairs(self):
        self.manifest['release_gates']['BACKEND-READY']['requires'] = ['E4B']
        self.assertTrue(any('current E4C' in e for e in self.errors()))

    def test_removed_cleanup_dependency_is_rejected(self):
        self.tasks['E3C']['integration_dependencies'].remove('M6')
        self.assertTrue(any('omits corrective tasks: M6' in e for e in self.errors()))

    def test_app_cannot_dispatch_early(self):
        del self.tasks['A2']['dispatch_after_gate']
        self.assertTrue(any('BACKEND-READY: A2' in e for e in self.errors()))

    def test_absent_brief_is_rejected(self):
        self.manifest['consumer_v1_closure']['handoff'] = 'missing-brief-for-test.md'
        self.assertTrue(any('Missing consumer closure handoff' in e for e in self.errors()))

    def test_owner_outside_release_cannot_hide_missing_coverage(self):
        self.manifest['consumer_v1_closure']['finding_tasks']['RV-01'] = ['X6']
        self.assertTrue(any('omits finding owners: X6' in e for e in self.errors()))


if __name__ == '__main__':
    unittest.main()
