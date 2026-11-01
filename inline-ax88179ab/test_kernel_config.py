"""Config integration tests, including values discarded by Kconfig resolution."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class KernelConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Path(self.directory.name) / '.config'
        self.fragment = HERE / 'autofs-debug.fragment'

    def run_tool(self, mode, success=True):
        result = subprocess.run([sys.executable, str(HERE / 'kernel-config.py'), mode,
                                 '--fragment', str(self.fragment), '--config', str(self.config)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def apply(self):
        self.config.write_text('# CONFIG_AUTOFS_FS is not set\nCONFIG_FUNCTION_TRACER=m\n'
                               'CONFIG_FUNCTION_TRACER=y\nCONFIG_USER_NS=y\n')
        self.run_tool('apply')

    def test_apply_replaces_duplicates_preserves_baseline_and_is_idempotent(self):
        self.apply()
        first = self.config.read_bytes()
        self.assertIn(b'CONFIG_USER_NS=y\n', first)
        self.assertEqual(first.count(b'# CONFIG_FUNCTION_TRACER is not set'), 1)
        self.assertNotIn(b'CONFIG_FUNCTION_TRACER=', first)
        self.run_tool('apply')
        self.assertEqual(first, self.config.read_bytes())
        self.run_tool('verify')

    def test_verification_rejects_each_missing_or_downgraded_resolved_option(self):
        self.apply()
        good = self.config.read_text()
        requests = [l for l in self.fragment.read_text().splitlines() if l.startswith('CONFIG_')]
        self.assertEqual(len(requests), 6)
        for entry in requests:
            key, value = entry.split('=')
            canonical = '# '+key+' is not set' if value == 'n' else entry
            replacements = [key+'=y', key+'=m'] if value == 'n' else ['', key+'=m', '# '+key+' is not set']
            for replacement in replacements:
                with self.subTest(entry=entry, replacement=replacement):
                    self.config.write_text(good.replace(canonical, replacement))
                    result = self.run_tool('verify', success=False)
                    self.assertIn(entry[:-2], result.stderr)

    def test_disabled_dependent_symbols_may_be_absent(self):
        self.apply()
        good = self.config.read_text()
        self.config.write_text(good.replace('# CONFIG_DYNAMIC_FTRACE is not set\n', '')
                              .replace('# CONFIG_FUNCTION_GRAPH_TRACER is not set\n', ''))
        self.run_tool('verify')

    def test_verification_rejects_duplicate_effective_values(self):
        self.apply()
        self.config.write_text(self.config.read_text() + 'CONFIG_AUTOFS_FS=y\n')
        self.run_tool('verify', success=False)

    def test_invalid_requests_do_not_modify_config(self):
        self.config.write_text('CONFIG_USER_NS=y\n')
        self.fragment = Path(self.directory.name) / 'bad.fragment'
        for text in ['', 'CONFIG_AUTOFS_FS=yes\n', 'CONFIG_AUTOFS_FS=y\nCONFIG_AUTOFS_FS=y\n']:
            with self.subTest(text=text):
                self.fragment.write_text(text)
                before = self.config.read_bytes()
                self.run_tool('apply', success=False)
                self.assertEqual(before, self.config.read_bytes())


if __name__ == '__main__':
    unittest.main()
