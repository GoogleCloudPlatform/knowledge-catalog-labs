"""Unit and security tests for enrichment.metadata.snapshot."""

import contextlib
import io
import pathlib
import tempfile
import unittest

from enrichment.metadata import snapshot


SAMPLE_ENTRY_MD = """---
name: projects/my-proj/locations/us/entryGroups/@bigquery/entries/my_table
resource: //bigquery.googleapis.com/projects/my-proj/datasets/my_ds/tables/my_table
---
# my-proj.my_ds.my_table
## Overview

Original documentation.
"""


class TestSnapshot(unittest.TestCase):
  def setUp(self):
    self._tmpdir = tempfile.TemporaryDirectory()
    self.root = pathlib.Path(self._tmpdir.name)
    self.snap_dir = self.root / 'snap'
    self.out_dir = self.root / 'out'
    self.snap_dir.mkdir()
    self.out_dir.mkdir()

  def tearDown(self):
    self._tmpdir.cleanup()

  def test_list_entries_returns_markdown_stems(self):
    (self.snap_dir / 'proj.ds.t1.md').write_text(SAMPLE_ENTRY_MD)
    (self.snap_dir / 'proj.ds.t2.md').write_text(SAMPLE_ENTRY_MD)
    (self.snap_dir / 'notes.txt').write_text('ignored')

    self.assertCountEqual(
      snapshot.list_entries(self.snap_dir),
      ['proj.ds.t1', 'proj.ds.t2'],
    )

  def test_update_entry_valid_table_name(self):
    table_name = 'my-proj.my_ds.my_table'
    (self.snap_dir / f'{table_name}.md').write_text(SAMPLE_ENTRY_MD)

    snapshot.update_entry(
      self.snap_dir, self.out_dir, table_name, 'Updated overview.'
    )

    out_path = self.out_dir / f'{table_name}.md'
    self.assertTrue(out_path.exists())
    updated = out_path.read_text()
    self.assertIn('Updated overview.', updated)
    self.assertNotIn('Original documentation.', updated)
    self.assertIn(
      'name: projects/my-proj/locations/us/entryGroups/@bigquery/entries/my_table',
      updated,
    )

  def test_update_entry_in_place(self):
    table_name = 'my-proj.my_ds.my_table'
    entry_path = self.snap_dir / f'{table_name}.md'
    entry_path.write_text(SAMPLE_ENTRY_MD)

    snapshot.update_entry(
      self.snap_dir, self.snap_dir, table_name, 'In-place update.'
    )

    self.assertIn('In-place update.', entry_path.read_text())

  def test_show_entry_valid_table_name(self):
    table_name = 'my-proj.my_ds.my_table'
    (self.snap_dir / f'{table_name}.md').write_text(SAMPLE_ENTRY_MD)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
      snapshot.show_entry(self.snap_dir, table_name)

    self.assertIn('Original documentation.', buf.getvalue())

  def test_safe_table_path_rejects_traversal_payloads(self):
    sibling_dir = self.root / 'snap_evil'
    sibling_dir.mkdir()
    (sibling_dir / 'secret.md').write_text(SAMPLE_ENTRY_MD)

    nested_dir = self.snap_dir / 'nested'
    nested_dir.mkdir()
    (nested_dir / 'inner.md').write_text(SAMPLE_ENTRY_MD)

    payloads = [
      '../secret',
      '../../etc/passwd',
      '../snap_evil/secret',
      'a/../../secret',
      '/etc/passwd',
      str(self.root / 'secret'),
      '..\\secret',
      'nested/inner',
      'nested\\inner',
      '',
      '.',
      '..',
      'valid\x00evil',
    ]

    for payload in payloads:
      with self.subTest(payload=payload):
        with self.assertRaises(ValueError):
          snapshot._safe_table_path(self.snap_dir, payload)

  def test_update_and_show_entry_block_path_traversal(self):
    outside = self.root / 'secret.md'
    outside.write_text(SAMPLE_ENTRY_MD)

    with self.assertRaises(ValueError):
      snapshot.update_entry(self.snap_dir, self.out_dir, '../secret', 'PWNED')
    self.assertEqual(outside.read_text(), SAMPLE_ENTRY_MD)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
      with self.assertRaises(ValueError):
        snapshot.show_entry(self.snap_dir, '../secret')
    self.assertEqual(buf.getvalue(), '')

  def test_symlink_escape_is_blocked(self):
    outside = self.root / 'secret.md'
    outside.write_text(SAMPLE_ENTRY_MD)
    symlink_in_snap = self.snap_dir / 'evil.md'
    symlink_in_snap.symlink_to(outside)

    with self.assertRaises(ValueError):
      snapshot.show_entry(self.snap_dir, 'evil')

    with self.assertRaises(ValueError):
      snapshot.update_entry(self.snap_dir, self.out_dir, 'evil', 'PWNED')
    self.assertEqual(outside.read_text(), SAMPLE_ENTRY_MD)

  def test_dangling_symlink_escape_in_out_dir_is_blocked(self):
    table_name = 'my-proj.my_ds.my_table'
    (self.snap_dir / f'{table_name}.md').write_text(SAMPLE_ENTRY_MD)

    outside_target = self.root / 'pwned.md'
    (self.out_dir / f'{table_name}.md').symlink_to(outside_target)

    with self.assertRaises(ValueError):
      snapshot.update_entry(self.snap_dir, self.out_dir, table_name, 'PWNED')
    self.assertFalse(outside_target.exists())


if __name__ == '__main__':
  unittest.main()
