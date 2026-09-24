"""
Data models for Dataplex Glossary Import/Export operations.

These dataclasses provide type-safe representations of Dataplex resources
and eliminate the need for fragile dictionary access patterns.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EntryReference:
    """Represents a source or target reference within an EntryLink."""
    name: str
    path: str = ""
    type: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EntryReference':
        """Create an EntryReference from a dictionary."""
        return cls(
            name=data.get('name', ''),
            path=data.get('path', ''),
            type=data.get('type')
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        result = {'name': self.name}
        if self.path:
            result['path'] = self.path
        if self.type:
            result['type'] = self.type
        return result


@dataclass
class EntryLink:
    """Represents a complete EntryLink for import/export operations."""
    name: str
    entryLinkType: str
    entryReferences: List[EntryReference] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EntryLink':
        """Create EntryLink from a dictionary."""
        # Handle both nested and flat dictionary formats
        entry_data = data.get('entryLink', data)
        refs = [EntryReference.from_dict(ref) for ref in entry_data.get('entryReferences', [])]
        return cls(
            name=entry_data.get('name', ''),
            entryLinkType=entry_data.get('entryLinkType', ''),
            entryReferences=refs
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'entryLink': {
                'name': self.name,
                'entryLinkType': self.entryLinkType,
                'entryReferences': [ref.to_dict() for ref in self.entryReferences]
            }
        }


@dataclass
class ParsedTermIdentifier:
    """Represents a parsed human-readable term identifier."""
    project_id: str
    location: str
    glossary_display_name: str
    term_display_name: str


@dataclass
class SpreadsheetRow:
    """Represents a row from the EntryLink import spreadsheet."""
    entry_link_type: str = ""
    source_name: str = ""
    source_id: str = ""
    column: str = ""
    target_name: str = ""
    target_id: str = ""
    row_number: int = 0

    def __init__(
        self,
        entry_link_type: str = "",
        source_name: str = "",
        source_id: str = "",
        column: str = "",
        target_name: str = "",
        target_id: str = "",
        source: Optional[str] = None,
        target: Optional[str] = None,
        source_entry: Optional[str] = None,
        target_entry: Optional[str] = None,
        source_path: Optional[str] = None,
        row_number: int = 0,
    ):
        self.entry_link_type = entry_link_type
        # Handle source (prefer explicit source_name/source_id, fallback to source/source_entry)
        self.source_name = source_name if source_name else (source_entry or source or "")
        self.source_id = source_id
        # Handle target (prefer explicit target_name/target_id, fallback to target/target_entry)
        self.target_name = target_name if target_name else (target_entry or target or "")
        self.target_id = target_id
        self.column = source_path if source_path is not None else column
        self.row_number = int(row_number) if row_number else 0

    @property
    def source(self) -> str:
        """Effective source identifier (prefers source_id, falls back to source_name)."""
        return self.source_id if self.source_id else self.source_name

    @property
    def target(self) -> str:
        """Effective target identifier (prefers target_id, falls back to target_name)."""
        return self.target_id if self.target_id else self.target_name

    @property
    def source_entry(self) -> str:
        return self.source

    @property
    def target_entry(self) -> str:
        return self.target

    @property
    def source_path(self) -> str:
        return self.column

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SpreadsheetRow':
        """Create a SpreadsheetRow from a dictionary supporting 6-column and legacy keys."""
        link_type = (
            data.get('entry_link_type') or data.get('Entry link type') or data.get('entryLinkType', '')
        ).strip()
        source_name = (
            data.get('source_name') or data.get('Source Name') or data.get('source') or data.get('Source') or data.get('source_entry') or data.get('sourceEntry', '')
        ).strip()
        source_id = (
            data.get('source_id') or data.get('Source ID') or data.get('sourceId', '')
        ).strip()
        target_name = (
            data.get('target_name') or data.get('Target Name') or data.get('target') or data.get('Target') or data.get('target_entry') or data.get('targetEntry', '')
        ).strip()
        target_id = (
            data.get('target_id') or data.get('Target ID') or data.get('targetId', '')
        ).strip()
        column = (
            data.get('column') or data.get('Column') or data.get('source_path') or data.get('sourcePath', '')
        ).strip()
        raw_row_num = data.get('row_number', 0)
        try:
            row_number = int(raw_row_num) if raw_row_num else 0
        except (ValueError, TypeError):
            row_number = 0
        return cls(
            entry_link_type=link_type,
            source_name=source_name,
            source_id=source_id,
            column=column,
            target_name=target_name,
            target_id=target_id,
            row_number=row_number,
        )
