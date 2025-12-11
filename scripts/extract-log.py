#!/usr/bin/env python3
"""
Extract log entries from XenServer/XCP-ng log files within a specific time range.

Usage:
    extract-log.py /var/log/xensource.log 1761920132 1761920200
    extract-log.py var/log/xensource.log 1730720532 1730720600 > relevant_logs.txt

Where the timestamps are epoch times (seconds since 1970-01-01 00:00:00 UTC).
You can get epoch time with: date '+%s'
Or for a specific date: date -d 'Oct 31 15:44:00' '+%s'

This script:
- Handles log rotation (xensource.log, xensource.log.1, xensource.log.2.gz, etc.)
- Parses XenServer log format: "Oct 31 15:44:23 [log content]"
- Extracts only lines within the specified time range
- Maintains chronological order
- Supports gzip-compressed log files
"""

import sys
import os
import glob
import gzip
from datetime import datetime
from typing import List, Tuple, Optional
import re


def parse_log_timestamp(month: str, day: str, time_str: str) -> Optional[int]:
    """
    Parse XenServer log timestamp to epoch time.

    Args:
        month: Month abbreviation (e.g., "Oct")
        day: Day of month (e.g., "31")
        time_str: Time string (e.g., "15:44:23")

    Returns:
        Epoch timestamp in seconds, or None if parsing fails
    """
    try:
        # XenServer logs don't include year, so we need to infer it
        # Assume current year, but handle year boundaries
        current_year = datetime.now().year

        # Build date string with current year
        date_str = f"{month} {day} {time_str} {current_year}"
        dt = datetime.strptime(date_str, "%b %d %H:%M:%S %Y")

        # If the log timestamp is in the future, it's probably from last year
        if dt.timestamp() > datetime.now().timestamp():
            date_str = f"{month} {day} {time_str} {current_year - 1}"
            dt = datetime.strptime(date_str, "%b %d %H:%M:%S %Y")

        return int(dt.timestamp())
    except (ValueError, AttributeError):
        return None


def open_log_file(filepath: str):
    """
    Open a log file, handling both plain text and gzip compression.

    Args:
        filepath: Path to the log file

    Returns:
        File handle that can be iterated line by line
    """
    if filepath.endswith('.gz'):
        return gzip.open(filepath, 'rt', encoding='utf-8', errors='replace')
    else:
        return open(filepath, 'r', encoding='utf-8', errors='replace')


def extract_logs_from_file(filepath: str, min_time: int, max_time: int) -> List[str]:
    """
    Extract log lines from a single file within the time range.

    Args:
        filepath: Path to the log file
        min_time: Minimum epoch timestamp (inclusive)
        max_time: Maximum epoch timestamp (exclusive)

    Returns:
        List of log lines within the time range
    """
    lines = []

    try:
        with open_log_file(filepath) as f:
            for line in f:
                line = line.rstrip('\n')
                if not line:
                    continue

                # Parse log line: "Oct 31 15:44:23 [rest of log]"
                # XenServer log format: Month Day Time [content]
                parts = line.split(None, 3)  # Split on whitespace, max 4 parts

                if len(parts) >= 3:
                    month, day, time_str = parts[0], parts[1], parts[2]

                    # Try to parse timestamp
                    timestamp = parse_log_timestamp(month, day, time_str)

                    if timestamp is not None:
                        # Check if within range
                        if timestamp < min_time:
                            # Before range - skip for efficiency
                            continue
                        elif min_time <= timestamp < max_time:
                            # Within range - include
                            lines.append(line)
                        else:
                            # After range - stop reading this file
                            break
                else:
                    # Line doesn't match expected format, might be continuation
                    # Include it if we're currently within range
                    if lines:  # If we've already found lines in range
                        lines.append(line)

    except Exception as e:
        print(f"Warning: Could not read {filepath}: {e}", file=sys.stderr)

    return lines


def natural_sort_key(filepath: str) -> List:
    """
    Generate sort key for natural sorting of log files.
    Handles: xensource.log, xensource.log.1, xensource.log.10, xensource.log.1.gz

    Args:
        filepath: Path to the log file

    Returns:
        Sort key for natural ordering
    """
    def convert(text):
        return int(text) if text.isdigit() else text.lower()

    # Remove .gz extension for sorting purposes
    path = filepath.replace('.gz', '')

    # Split path into alphabetic and numeric parts
    parts = re.split(r'(\d+)', path)
    return [convert(part) for part in parts]


def extract_logs(base_log_path: str, min_time: int, max_time: int) -> List[str]:
    """
    Extract logs from all rotated log files within the time range.

    Args:
        base_log_path: Base path to the log file (e.g., /var/log/xensource.log)
        min_time: Minimum epoch timestamp (inclusive)
        max_time: Maximum epoch timestamp (exclusive)

    Returns:
        List of log lines within the time range, in chronological order
    """
    # Find all related log files (including rotated and compressed)
    log_pattern = f"{base_log_path}*"
    log_files = glob.glob(log_pattern)

    if not log_files:
        print(f"Error: No log files found matching {log_pattern}", file=sys.stderr)
        return []

    # Sort log files naturally (oldest to newest)
    # xensource.log is current, xensource.log.1 is previous, etc.
    log_files.sort(key=natural_sort_key)

    all_lines = []

    # Process each log file
    for log_file in log_files:
        lines = extract_logs_from_file(log_file, min_time, max_time)
        all_lines.extend(lines)

    return all_lines


def main():
    """Main entry point for the script."""
    if len(sys.argv) != 4:
        print("Usage: extract-log.py <log_file> <start_epoch> <end_epoch>", file=sys.stderr)
        print("", file=sys.stderr)
        print("Example:", file=sys.stderr)
        print("  extract-log.py /var/log/xensource.log 1761920132 1761920200", file=sys.stderr)
        print("  extract-log.py var/log/xensource.log 1730720532 1730720600", file=sys.stderr)
        print("", file=sys.stderr)
        print("Get current epoch time: date '+%s'", file=sys.stderr)
        print("Get epoch for specific date: date -d 'Oct 31 15:44:00' '+%s'", file=sys.stderr)
        sys.exit(1)

    base_log_path = sys.argv[1]

    try:
        min_time = int(sys.argv[2])
        max_time = int(sys.argv[3])
    except ValueError:
        print("Error: start_epoch and end_epoch must be integers", file=sys.stderr)
        sys.exit(1)

    if min_time >= max_time:
        print("Error: start_epoch must be less than end_epoch", file=sys.stderr)
        sys.exit(1)

    # Extract and print logs
    lines = extract_logs(base_log_path, min_time, max_time)

    if not lines:
        print(f"No log entries found in time range {min_time} to {max_time}", file=sys.stderr)
        sys.exit(0)

    # Print all extracted lines
    for line in lines:
        print(line)

    # Print summary to stderr
    print(f"\n# Extracted {len(lines)} log lines from {min_time} to {max_time}", file=sys.stderr)


if __name__ == "__main__":
    main()
