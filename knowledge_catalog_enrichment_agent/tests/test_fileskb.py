"""Unit and security tests for tools.fileskb.main."""

import importlib
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest


def _load_fileskb_module():
  if importlib.util.find_spec('mcp') is None and 'mcp' not in sys.modules:
    mcp_pkg = types.ModuleType('mcp')
    mcp_server = types.ModuleType('mcp.server')
    mcp_fastmcp = types.ModuleType('mcp.server.fastmcp')

    class _DummyFastMCP:
      def __init__(self, name):
        self.name = name

      def tool(self, name=None):
        def decorator(fn):
          return fn
        return decorator

    mcp_fastmcp.FastMCP = _DummyFastMCP
    sys.modules['mcp'] = mcp_pkg
    sys.modules['mcp.server'] = mcp_server
    sys.modules['mcp.server.fastmcp'] = mcp_fastmcp

  return importlib.import_module('tools.fileskb.main')


fileskb = _load_fileskb_module()


class TestFilesKB(unittest.TestCase):
  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self.root = pathlib.Path(self._tmpdir.name)
    self.kb_dir = self.root / 'kb'
    self.kb_dir.mkdir()
    self.sub_dir = self.kb_dir / 'subdir'
    self.sub_dir.mkdir()

    (self.kb_dir / 'overview.md').write_text('# Overview\nQuarterly revenue metrics.\n')
    (self.sub_dir / 'doc.md').write_text('# Subdoc\nDetailed revenue formula.\n')

    # Sibling directory sharing the exact KB_ROOT prefix ('kb' -> 'kb_secret')
    self.sibling_dir = self.root / 'kb_secret'
    self.sibling_dir.mkdir()
    (self.sibling_dir / 'creds.md').write_text('TOP_SECRET_SIBLING_TOKEN\n')

    self.outside_file = self.root / 'outside.md'
    self.outside_file.write_text('TOP_SECRET_OUTSIDE_TOKEN\n')

    self._orig_kb_root = fileskb.KB_ROOT
    fileskb.KB_ROOT = self.kb_dir.resolve()

  def tearDown(self):
    fileskb.KB_ROOT = self._orig_kb_root
    self._tmpdir.cleanup()

  def test_list_read_and_search_normal_usage(self):
    listing = fileskb.list_contents('')
    self.assertIn('overview.md | overview.md | file', listing)
    self.assertIn('subdir | subdir | directory', listing)
    self.assertEqual(fileskb.list_contents('/'), listing)

    sub_listing = fileskb.list_contents('subdir')
    self.assertIn('doc.md | subdir/doc.md | file', sub_listing)

    self.assertIn('Quarterly revenue', fileskb.read_file('overview.md'))
    self.assertIn('Quarterly revenue', fileskb.read_file('/overview.md'))
    self.assertIn('Detailed revenue', fileskb.read_file('subdir/doc.md'))

    matches = fileskb.search_content('revenue')
    self.assertEqual(len(matches), 2)
    matched_files = {m['file'] for m in matches}
    self.assertEqual(matched_files, {'overview.md', 'subdir/doc.md'})

    single_matches = fileskb.search_content('revenue', 'overview.md')
    self.assertEqual(len(single_matches), 1)
    self.assertEqual(single_matches[0]['file'], 'overview.md')

  def test_sibling_prefix_and_traversal_payloads_blocked(self):
    payloads = [
      '../kb_secret/creds.md',
      '///../kb_secret/creds.md',
      '../outside.md',
      '../../etc/passwd',
      'subdir/../../kb_secret/creds.md',
      '..\\kb_secret\\creds.md',
      '..\\outside.md',
      'overview.md\x00.txt',
    ]

    for payload in payloads:
      with self.subTest(payload=payload):
        with self.assertRaises(ValueError):
          fileskb._safe_path(payload)
        with self.assertRaises(ValueError):
          fileskb.read_file(payload)
        with self.assertRaises(ValueError):
          fileskb.list_contents(payload)
        with self.assertRaises(ValueError):
          fileskb.search_content('TOP_SECRET', payload)

  def test_symlink_outside_kb_root_blocked_in_read_list_and_search(self):
    leak_link = self.kb_dir / 'leak.md'
    leak_link.symlink_to(self.outside_file)

    with self.assertRaises(ValueError):
      fileskb.read_file('leak.md')

    listing = fileskb.list_contents('')
    self.assertNotIn('leak.md', listing)

    matches = fileskb.search_content('TOP_SECRET')
    self.assertEqual(matches, [])


if __name__ == '__main__':
  unittest.main()
