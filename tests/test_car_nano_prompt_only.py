import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CarNanoPromptOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nano-run-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.brief = self.folder / 'brief.json'
        self.sources = self.folder / 'sources.json'
        self.sources.write_text(json.dumps({
            'format': 'liu.input-sources.v1',
            'messages': [{'id': 'request', 'actor': 'user',
                          'text': 'Create an open-identity vehicle photograph.',
                          'origin': 'host_message_record', 'locator': 'test fixture'}],
        }), encoding='utf-8')

    def prepare(self, target, kind, entry='hex_agent'):
        self.brief.write_text(json.dumps({
            'title': 'Vehicle test', 'request': 'Create an open-identity vehicle photograph.',
            'domain': 'vehicle', 'scope': 'create', 'vehicle_identity': 'open',
            'targets': [target], 'references': [],
        }), encoding='utf-8')
        run = self.folder / (target + '-' + str(kind))
        command = [sys.executable, '-m', entry]
        if entry == 'hex_agent':
            command.append('car')
        command += ['prepare', '--brief', str(self.brief), '--sources', str(self.sources),
                    '--workflow-profile', 'full-flow-v1', '--out', str(run)]
        if kind is not None:
            command += ['--deliverable-kind', kind]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        return result, run

    def test_nano_prompt_only_and_inspection(self):
        result, run = self.prepare('nano', 'prompt_only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)['configured_tool'])
        packet = json.loads((run / 'packet.json').read_text())
        self.assertNotIn('nano', packet.get('compilation_runtimes', {}))
        self.assertIn('nano', packet['model_profiles'])
        for command in ('preview', 'freeze-input'):
            with self.subTest(command=command):
                result = subprocess.run([
                    sys.executable, '-m', 'hex_agent', 'car', command, '--run', str(run),
                    '--solution', str(self.folder / 'unused.json'),
                ], cwd=ROOT, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('prompt_only', result.stderr)
                self.assertIn('未绑定生图工具', result.stderr)

    def test_nano_rejects_other_deliverables(self):
        for kind in ('image', 'local_edit', None):
            with self.subTest(kind=kind):
                result, run = self.prepare('nano', kind)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('nano 仅支持 --deliverable-kind prompt_only', result.stderr)
                self.assertFalse(run.exists())

    def test_bound_targets_keep_runtime(self):
        from liu_agent.car_photography import TOOLS
        from liu_agent.generation_spec import host_capabilities
        for target in ('seedream', 'image'):
            for kind in ('prompt_only', 'image'):
                with self.subTest(target=target, kind=kind):
                    result, run = self.prepare(target, kind)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)['configured_tool'], TOOLS[target])
                    packet = json.loads((run / 'packet.json').read_text())
                    self.assertEqual(packet['compilation_runtimes'][target],
                                     host_capabilities(TOOLS[target]))

    def test_compatibility_entry(self):
        result, _ = self.prepare('nano', 'prompt_only', 'cl_hex.car_photography')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
