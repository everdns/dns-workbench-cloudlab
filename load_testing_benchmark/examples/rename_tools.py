"""Rename old tool names in CSV files and raw output directories.

Usage:
    # Update a CSV file in-place:
    python examples/rename_tools.py --csv /path/to/results.csv

    # Rename files in a raw output directory:
    python examples/rename_tools.py --raw-dir /path/to/results/

    # Both at once:
    python examples/rename_tools.py --csv results.csv --raw-dir /path/to/results/

    # Dry run (show what would change without modifying anything):
    python examples/rename_tools.py --csv results.csv --raw-dir /path/to/results/ --dry-run
"""

import argparse
import csv
import os
import sys
import tempfile

RENAMES = {
    "dns64perfpp-dnsworkbench": "dns64perfpp-dnsworkbench",
    "dnsperf-workbench-slice": "dnsperf-dnsworkbench-slice",
    "dnsperf-workbench-lencse": "dnsperf-dnsworkbench-lencse",
    "dnspyre-workbench": "dnspyre-dnsworkbench",
}


def rename_csv(csv_path, dry_run=False):
    """Replace old tool names in the 'tool' column of a CSV file."""
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        if "tool" not in reader.fieldnames:
            print(f"  No 'tool' column in {csv_path}, skipping")
            return
        rows = list(reader)
        fieldnames = reader.fieldnames

    changed = 0
    for row in rows:
        old = row["tool"]
        if old in RENAMES:
            row["tool"] = RENAMES[old]
            changed += 1

    if changed == 0:
        print(f"  No rows to rename in {csv_path}")
        return

    if dry_run:
        print(f"  Would rename {changed} rows in {csv_path}")
        return

    # Write to a temp file then replace, to avoid data loss on crash
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(csv_path), suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_path, csv_path)
    except BaseException:
        os.unlink(tmp_path)
        raise

    print(f"  Renamed {changed} rows in {csv_path}")


def rename_raw_dir(raw_dir, dry_run=False):
    """Rename raw output files that contain old tool names."""
    # Look in raw/, timestamps/, and the directory itself
    search_dirs = [raw_dir]
    for sub in ("raw", "timestamps"):
        sub_path = os.path.join(raw_dir, sub)
        if os.path.isdir(sub_path):
            search_dirs.append(sub_path)

    changed = 0
    for search_dir in search_dirs:
        for filename in sorted(os.listdir(search_dir)):
            new_filename = filename
            for old_name, new_name in RENAMES.items():
                if old_name in new_filename:
                    new_filename = new_filename.replace(old_name, new_name)

            if new_filename != filename:
                old_path = os.path.join(search_dir, filename)
                new_path = os.path.join(search_dir, new_filename)
                if dry_run:
                    print(f"  Would rename: {filename} -> {new_filename}")
                else:
                    os.rename(old_path, new_path)
                    print(f"  Renamed: {filename} -> {new_filename}")
                changed += 1

    if changed == 0:
        print(f"  No files to rename in {raw_dir}")
    else:
        label = "Would rename" if dry_run else "Renamed"
        print(f"  {label} {changed} files total")


def main():
    parser = argparse.ArgumentParser(
        description="Rename old tool names in CSV files and raw output directories"
    )
    parser.add_argument("--csv", nargs="+", help="Path(s) to results CSV file(s)")
    parser.add_argument("--raw-dir", nargs="+", help="Path(s) to raw output directory(ies)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without modifying anything")
    args = parser.parse_args()

    if not args.csv and not args.raw_dir:
        parser.print_help()
        sys.exit(1)

    if args.dry_run:
        print("=== DRY RUN ===\n")

    print("Renames:")
    for old, new in RENAMES.items():
        print(f"  {old} -> {new}")
    print()

    if args.csv:
        for csv_path in args.csv:
            print(f"CSV: {csv_path}")
            rename_csv(csv_path, dry_run=args.dry_run)

    if args.raw_dir:
        for raw_dir in args.raw_dir:
            print(f"Raw dir: {raw_dir}")
            rename_raw_dir(raw_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
