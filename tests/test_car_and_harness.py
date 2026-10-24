"""Small offline checks for the car entry, the harness order and the public example."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from liu_agent.car_photography import normalize_brief, validate_vehicle_identity  # noqa: E402
from liu_agent.harness import definition  # noqa: E402
from liu_agent.io import AgentError  # noqa: E402

EXAMPLE = ROOT / 'examples' / 'vehicle-brief.json'


def vehicle_brief(**changes):
    value = {'title': 'Vehicle test', 'request': 'Create a vehicle photograph.',
             'domain': 'vehicle', 'scope': 'create', 'vehicle_identity': 'open',
             'targets': ['seedream'], 'references': []}
    value.update(changes)
    return value


class CarIdentityTests(unittest.TestCase):
    def test_open_identity_cannot_drop_identity_photos(self):
        refs = [{'id': 'car', 'role': 'identity'}]
        with self.assertRaises(AgentError):
            validate_vehicle_identity(vehicle_brief(), refs)
        with self.assertRaises(AgentError):
            validate_vehicle_identity(vehicle_brief(vehicle_identity='preserve'), [])

    def test_open_identity_cannot_skip_open_identity_question(self):
        need = {'question_records': [{'decision_type': 'identity', 'status': 'open'}]}
        with self.assertRaises(AgentError):
            validate_vehicle_identity(vehicle_brief(), [], need)


class CarTargetTests(unittest.TestCase):
    def test_rejects_multiple_or_unbound_targets(self):
        for targets in (['image', 'nano', 'midjourney', 'seedream'], ['nano'], ['midjourney']):
            with self.subTest(targets=targets):
                with self.assertRaises(AgentError):
                    normalize_brief(vehicle_brief(targets=targets), ROOT)
        for target in ('seedream', 'image'):
            with self.subTest(target=target):
                self.assertEqual(normalize_brief(vehicle_brief(targets=[target]), ROOT)['targets'], [target])


class HarnessTests(unittest.TestCase):
    def test_definition_has_ten_stages_in_order(self):
        steps = [step['id'] for step in definition()['steps']]
        self.assertEqual(steps, ['intent', 'references', 'design', 'compile', 'verify',
                                 'authorize', 'execute', 'evaluate', 'deliver', 'feedback'])


class ExampleBriefTests(unittest.TestCase):
    def test_vehicle_example_prepares(self):
        brief = json.loads(EXAMPLE.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(prefix='hex-example-') as folder:
            sources = Path(folder) / 'sources.json'
            sources.write_text(json.dumps({
                'format': 'liu.input-sources.v1',
                'messages': [{'id': 'request', 'actor': 'user', 'text': brief['request'],
                              'origin': 'host_message_record', 'locator': 'test fixture'}],
            }, ensure_ascii=False), encoding='utf-8')
            run = Path(folder) / 'run'
            result = subprocess.run(
                [sys.executable, '-m', 'hex_agent', 'car', 'prepare', '--brief', str(EXAMPLE),
                 '--sources', str(sources), '--out', str(run)],
                cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['target'], 'seedream')
            self.assertTrue((run / 'packet.json').is_file())


if __name__ == '__main__':
    unittest.main()
