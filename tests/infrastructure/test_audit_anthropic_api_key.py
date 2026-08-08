"""Tests for scripts/audit-anthropic-api-key.ps1 — masking logic + fixture parsing.

The PowerShell script cannot run in this environment (no pwsh). These tests
validate the core masking and detection rules re-implemented in Python so they
can run in CI / on-device testing.

The actual PS script behaviour (env-var lookup via [Environment]::GetEnvironmentVariable,
file I/O, -Apply mutation) must be validated by running it on a Windows device.
"""

import re
import tempfile
import unittest
from pathlib import Path


# ── Masking logic (replica of PS Mask-Key) ──────────────────────────────────

def mask_key(key: str) -> str:
    """Match PS Mask-Key: first4...last4  (len=N)."""
    if not key:
        return '(empty)'
    if len(key) <= 12:
        prefix = key[:min(2, len(key))]
        suffix = key[max(len(key) - 2, 0):]
        return prefix + '...' + suffix
    return f"{key[:4]}...{key[-4:]}  (len={len(key)})"


# ── Detection helpers (replica of PS env-file scanning) ─────────────────────

_ENV_VAR_RE = re.compile(r'^ANTHROPIC_API_KEY=(.*)$')

def find_in_env_file(path: Path):
    """Scan a .env file for ANTHROPIC_API_KEY, return its value or None."""
    if not path.exists():
        return None
    for line in path.read_text(errors='replace').splitlines():
        m = _ENV_VAR_RE.match(line.strip())
        if m:
            val = m.group(1).strip('"').strip("'")
            return val if val else ''
    return None


_DOTFILE_RE = re.compile(r'^(?:export\s+)?ANTHROPIC_API_KEY=(.*)$')

def find_in_dotfile(path: Path) -> list[dict]:
    """Scan a dotfile for ANTHROPIC_API_KEY exports/assignments."""
    results = []
    if not path.exists():
        return results
    for i, line in enumerate(path.read_text(errors='replace').splitlines(), start=1):
        m = _DOTFILE_RE.match(line.strip())
        if m:
            val = m.group(1).strip('"').strip("'") if m.group(1) else ''
            results.append({'line': i, 'value': val})
    return results


# ── Tests: masking ──────────────────────────────────────────────────────────

class TestMaskKey(unittest.TestCase):
    def test_normal_key(self):
        """First 4 + last 4 + length for a typical Anthropic key."""
        key = 'sk-ant-abcdefghijklmnopqrstuvwxyz'
        result = mask_key(key)
        self.assertEqual(result, 'sk-a...wxyz  (len=33)')

    def test_short_key(self):
        """Key <= 12 chars uses short-path."""
        self.assertEqual(mask_key('abcdef'), 'ab...ef')
        self.assertEqual(mask_key('a'), 'a...a')

    def test_empty_key(self):
        """Null/empty returns explicit marker."""
        self.assertEqual(mask_key(''), '(empty)')
        self.assertEqual(mask_key(None), '(empty)')

    def test_exactly_12_chars(self):
        """Boundary: key length exactly 12 uses short path."""
        self.assertEqual(mask_key('abcdefghijkl'), 'ab...kl')

    def test_13_chars(self):
        """Boundary: 13 chars hits the long path."""
        self.assertEqual(mask_key('abcdefghijklm'), 'abcd...jklm  (len=13)')


# ── Tests: env file parsing ─────────────────────────────────────────────────

class TestFindInEnvFile(unittest.TestCase):
    def test_finds_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env_file.write_text(
                'SUPABASE_URL=https://example.com\n'
                'ANTHROPIC_API_KEY=sk-ant-test-key-value-here\n'
                'VOYAGE_API_KEY=pa-voyage-key\n'
            )
            val = find_in_env_file(env_file)
            self.assertEqual(val, 'sk-ant-test-key-value-here')

    def test_finds_quoted_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env_file.write_text('ANTHROPIC_API_KEY="sk-ant-quoted-key"\n')
            val = find_in_env_file(env_file)
            self.assertEqual(val, 'sk-ant-quoted-key')

    def test_finds_single_quoted_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env_file.write_text("ANTHROPIC_API_KEY='sk-ant-single-quoted'\n")
            val = find_in_env_file(env_file)
            self.assertEqual(val, 'sk-ant-single-quoted')

    def test_empty_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env_file.write_text('ANTHROPIC_API_KEY=\n')
            val = find_in_env_file(env_file)
            self.assertEqual(val, '')

    def test_missing_file(self):
        self.assertIsNone(find_in_env_file(Path('/nonexistent/.env')))


# ── Tests: dotfile parsing ──────────────────────────────────────────────────

class TestFindInDotfile(unittest.TestCase):
    def test_export_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotfile = Path(tmp) / '.bashrc'
            dotfile.write_text('export ANTHROPIC_API_KEY=sk-ant-dotfile-key\n')
            results = find_in_dotfile(dotfile)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['value'], 'sk-ant-dotfile-key')

    def test_assignment_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotfile = Path(tmp) / '.zshrc'
            dotfile.write_text('ANTHROPIC_API_KEY=sk-ant-zsh-key\n')
            results = find_in_dotfile(dotfile)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['value'], 'sk-ant-zsh-key')

    def test_multiple_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotfile = Path(tmp) / '.profile'
            dotfile.write_text(
                'export ANTHROPIC_API_KEY=sk-ant-key1\n'
                'export PATH=$PATH:/something\n'
                'export ANTHROPIC_API_KEY=sk-ant-key2\n'
            )
            results = find_in_dotfile(dotfile)
            self.assertEqual(len(results), 2)

    def test_empty_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotfile = Path(tmp) / '.bashrc'
            dotfile.write_text('export ANTHROPIC_API_KEY=\n')
            results = find_in_dotfile(dotfile)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['value'], '')

    def test_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotfile = Path(tmp) / '.bashrc'
            dotfile.write_text('export PATH=$PATH:/usr/bin\n')
            self.assertEqual(find_in_dotfile(dotfile), [])


# ── Integration: mask + detect end-to-end ───────────────────────────────────

class TestIntegration(unittest.TestCase):
    def test_detect_and_mask(self):
        """Parse a fixture env file, find key, mask it — raw key must not appear."""
        raw_key = 'sk-ant-abcdefghijklmnopqrstuvwxyz'

        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env_file.write_text(f'ANTHROPIC_API_KEY={raw_key}\n')

            val = find_in_env_file(env_file)
            self.assertEqual(val, raw_key)

            masked = mask_key(val)
            self.assertIn('sk-a', masked)          # first 4
            self.assertIn('wxyz', masked)          # last 4
            self.assertNotIn(raw_key, masked)       # full key must never appear
            self.assertIn('len=33', masked)

    def test_dotfile_detect_and_mask(self):
        """Raw key value must not appear after masking."""
        raw_key = 'sk-ant-dotfile-secret-key-value'

        with tempfile.TemporaryDirectory() as tmp:
            dotfile = Path(tmp) / '.bashrc'
            dotfile.write_text(f'export ANTHROPIC_API_KEY={raw_key}\n')

            results = find_in_dotfile(dotfile)
            self.assertEqual(len(results), 1)
            val = results[0]['value']

            masked = mask_key(val)
            self.assertIn('sk-a', masked)
            self.assertNotIn(raw_key, masked)   # critical: never output raw


if __name__ == '__main__':
    unittest.main()
