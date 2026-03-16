#!/bin/bash

OUTPUT=~/startup_logs.md
GENI_STARTUP=/var/tmp/geni_startup.*

> "$OUTPUT"

# Find all startup-N.txt files sorted numerically
for log_file in $(ls /var/tmp/startup-*.txt 2>/dev/null | sort -t'-' -k2 -n); do
    # Extract N from startup-N.txt
    n=$(echo "$log_file" | grep -oP 'startup-\K[0-9]+')

    echo "# Execution $n" >> "$OUTPUT"
    echo "" >> "$OUTPUT"

    # Extract the command from geni_startup that redirects to this log file
    cmd=$(grep ">/var/tmp/startup-${n}.txt" $GENI_STARTUP 2>/dev/null | sed "s| >/var/tmp/startup-${n}\.txt 2>&1||")
    echo "## Command" >> "$OUTPUT"
    echo '```' >> "$OUTPUT"
    echo "${cmd:-unknown}" >> "$OUTPUT"
    echo '```' >> "$OUTPUT"
    echo "" >> "$OUTPUT"

    # Read exit status
    status_file="/var/tmp/startup-${n}.status"
    if [ -f "$status_file" ]; then
        status=$(cat "$status_file")
    else
        status="unknown"
    fi
    echo "## Status" >> "$OUTPUT"
    echo '```' >> "$OUTPUT"
    echo "$status" >> "$OUTPUT"
    echo '```' >> "$OUTPUT"
    echo "" >> "$OUTPUT"

    # Read log content
    echo "## Log" >> "$OUTPUT"
    echo '```' >> "$OUTPUT"
    cat "$log_file" >> "$OUTPUT"
    echo '```' >> "$OUTPUT"
    echo "" >> "$OUTPUT"
    echo "---" >> "$OUTPUT"
    echo "" >> "$OUTPUT"
done

echo "Logs collected to $OUTPUT"
