#!/usr/bin/env python3
"""
csv_files_generator.py

Generate per-glossary CSVs (categories.csv and terms.csv) for glossaries that
were already created by your bulk creation script.

Reads input CSV files (categories and terms) and duplicates them into each glossary folder.

Defaults:
  - reads glossary ids from: ./datasets/_created_glossaries.txt
  - writes to: ./datasets/<glossary>/{categories.csv,terms.csv}
"""
from pathlib import Path
import shutil
import argparse
import sys


def copy_csv_file(src_path: Path, dst_path: Path):
    """Copy a CSV file from source to destination."""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dst_path)
    print(f"Copied: {src_path} -> {dst_path}")

def load_glossary_ids(created_file: Path):
    if not created_file.exists():
        raise FileNotFoundError(f"Glossary list not found: {created_file}")
    data = []
    with created_file.open("r", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            data.append(s)
    return data

def parse_args():
    p = argparse.ArgumentParser(description="Generate categories/terms CSVs for existing glossaries (synonyms + related, no definitions).")
    p.add_argument("--created-file", default="./datasets/_created_glossaries.txt",
                   help="File listing created glossary ids (one per line). Default: ./datasets/_created_glossaries.txt")
    p.add_argument("--output-root", default="./datasets",
                   help="Root folder where per-glossary folders will be created. Default: ./datasets")
    p.add_argument("--write-error-files", action="store_true",
                   help="Also write sample error/empty CSVs alongside valid CSVs.")
    return p.parse_args()

def main(args=None):
    parser = argparse.ArgumentParser(description="Copy input CSV files to each glossary folder.")
    parser.add_argument("--created-file", default="./datasets/_created_glossaries.txt",
                   help="File listing created glossary ids (one per line). Default: ./datasets/_created_glossaries.txt")
    parser.add_argument("--terms-csv", required=True,
                   help="Path to the terms CSV file to duplicate")
    parser.add_argument("--categories-csv", required=True,
                   help="Path to the categories CSV file to duplicate")
    parser.add_argument("--output-root", default="./datasets",
                   help="Root folder where per-glossary folders will be created. Default: ./datasets")
    parsed_args = parser.parse_args(args)

    terms_src = Path(parsed_args.terms_csv)
    categories_src = Path(parsed_args.categories_csv)
    created_file = Path(parsed_args.created_file)
    output_root = Path(parsed_args.output_root)

    # Validate source files exist
    if not terms_src.exists():
        print(f"Terms CSV file not found: {terms_src}", file=sys.stderr)
        sys.exit(1)
    if not categories_src.exists():
        print(f"Categories CSV file not found: {categories_src}", file=sys.stderr)
        sys.exit(1)

    try:
        glossaries = load_glossary_ids(created_file)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if not glossaries:
        print(f"No glossary ids found in {created_file}", file=sys.stderr)
        sys.exit(1)

    created_count = 0
    skipped = []
    for g in glossaries:
        base = output_root / g 
        cat_dst = base / "categories.csv"
        terms_dst = base / "terms.csv"

        try:
            copy_csv_file(categories_src, cat_dst)
            copy_csv_file(terms_src, terms_dst)
            created_count += 1
            print(f"Copied CSVs for glossary: {g}")
        except Exception as e:
            print(f"Failed to copy CSVs for {g}: {e}", file=sys.stderr)
            skipped.append((g, str(e)))

    print(f"\nDone. Copied CSVs for {created_count} glossaries. Skipped: {len(skipped)}")
    if skipped:
        print("Skipped details:")
        for g, err in skipped:
            print(f" - {g}: {err}")
        if args is None:
            sys.exit(2)
    elif args is None:
        sys.exit(0)

if __name__ == "__main__":
    main()
