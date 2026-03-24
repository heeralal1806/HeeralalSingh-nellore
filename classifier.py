"""
UC-0A: Complaint Classifier
----------------------------
Reads a city CSV file of civic complaints, classifies each complaint by
category and severity using Claude API, and writes results to
results_[city].csv.

Usage:
    python classifier.py                        # auto-detects city CSVs
    python classifier.py test_pune.csv          # specific file
    python classifier.py test_hyderabad.csv

Output:
    results_pune.csv          (or matching city name)

Each output row contains all original columns plus:
    category, severity, reason

CRAFT loop evidence:
    After classification, a validation report is printed showing
    row count match, invalid fields, and severity-blindness warnings.
"""

import csv
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'city-test-files')
OUTPUT_DIR = os.path.dirname(__file__)

API_URL = 'https://api.anthropic.com/v1/messages'
MODEL = 'claude-sonnet-4-20250514'
MAX_TOKENS = 1000
BATCH_SIZE = 5       # complaints per API call
RETRY_LIMIT = 3
RETRY_DELAY = 2      # seconds between retries


# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

VALID_CATEGORIES = {
    'Roads', 'Water Supply', 'Sanitation', 'Electricity',
    'Public Safety', 'Health', 'Trees & Parks', 'Noise', 'Other'
}
VALID_SEVERITIES = {'Critical', 'High', 'Medium', 'Low'}

CRITICAL_KEYWORDS = [
    'child', 'children', 'school', 'hospital', 'injur', 'death', 'died',
    'collapse', 'collapsed', 'live wire', 'electrocut', 'flood', 'flooding',
    'outbreak', 'epidemic', 'fire', 'burning', 'accident', 'emergency'
]


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a civic complaint triage officer for a municipal corporation. "
    "Your classifications feed directly into an emergency dispatch queue. "
    "Wrong severity delays emergency response. Accuracy is mandatory."
)


def build_batch_prompt(complaints: list) -> str:
    numbered = '\n'.join(
        f'{i+1}. {c}' for i, c in enumerate(complaints)
    )
    return f"""Classify each civic complaint below.

Return ONLY a valid JSON array — no markdown, no preamble, no explanation outside the array.
One object per complaint, in the same order as listed.

CATEGORIES (pick the most specific):
- Roads: pothole, road, street, pavement, footpath, divider, traffic signal, speed breaker
- Water Supply: water, pipe, leakage, no water, tap, borewell, supply, drinking water
- Sanitation: garbage, waste, drain, sewage, stench, cleaning, sweeping, dustbin, litter
- Electricity: power, light, streetlight, electricity, transformer, wire, outage, blackout
- Public Safety: crime, harassment, theft, assault, danger, unsafe, attack, violence
- Health: disease, epidemic, mosquito, dengue, malaria, contamination, outbreak, hospital
- Trees & Parks: tree, park, garden, branch, fallen tree, encroachment
- Noise: noise, sound, loudspeaker, construction noise, disturbance
- Other: ONLY if absolutely no above category matches

SEVERITY (check top-down, Critical takes priority):
- Critical: mentions injury, death, child, school, hospital, collapse, live wire, flood, outbreak, fire, accident
- High: affects many people / whole area / main road, major service disruption, days without service, risk of injury
- Medium: inconvenience to residents, partial service disruption, non-urgent repair
- Low: minor aesthetic issue, isolated inconvenience, zero safety risk

IMPORTANT:
- The "reason" field is MANDATORY for every complaint.
- Never assign Low or Medium when a Critical keyword is present.
- Never use "Other" if a keyword from the category list appears in the complaint.

COMPLAINTS:
{numbered}

Return exactly this structure:
[
  {{"id": 1, "category": "...", "severity": "...", "reason": "..."}},
  {{"id": 2, "category": "...", "severity": "...", "reason": "..."}},
  ...
]"""


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def call_claude_api(prompt: str) -> str:
    payload = {
        'model': MODEL,
        'max_tokens': MAX_TOKENS,
        'system': SYSTEM_PROMPT,
        'messages': [{'role': 'user', 'content': prompt}]
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01',
        },
        method='POST'
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode('utf-8'))
    blocks = result.get('content', [])
    return ''.join(b.get('text', '') for b in blocks if b.get('type') == 'text')


def parse_json_response(text: str) -> list:
    """Extract JSON array from API response, stripping any markdown fences."""
    text = text.strip()
    # Strip ```json ... ``` fences if present
    text = re.sub(r'^```[a-z]*\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    text = text.strip()
    return json.loads(text)


def classify_batch_with_retry(complaints: list) -> list:
    prompt = build_batch_prompt(complaints)
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            raw = call_claude_api(prompt)
            results = parse_json_response(raw)
            # Validate we got back the right number of items
            if len(results) != len(complaints):
                raise ValueError(
                    f'Expected {len(complaints)} results, got {len(results)}'
                )
            return results
        except Exception as e:
            print(f'    [Attempt {attempt}/{RETRY_LIMIT}] Error: {e}')
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY)
            else:
                # Return fallback rows so output row count still matches
                print('    [WARN] Falling back to placeholder for this batch.')
                return [
                    {
                        'id': i + 1,
                        'category': 'Other',
                        'severity': 'Medium',
                        'reason': f'Classification failed after {RETRY_LIMIT} attempts.'
                    }
                    for i in range(len(complaints))
                ]


# ---------------------------------------------------------------------------
# Skills: validation
# ---------------------------------------------------------------------------

def fast_severity_hint(complaint_text: str) -> str:
    """Returns 'Critical' if safety keywords found, else None."""
    text = complaint_text.lower()
    for kw in CRITICAL_KEYWORDS:
        if kw in text:
            return 'Critical'
    return None


def validate_output(input_rows: list, classified: list) -> list:
    issues = []
    if len(input_rows) != len(classified):
        issues.append(
            f'Row count mismatch: input={len(input_rows)}, '
            f'classified={len(classified)}'
        )
    for i, (orig, cls) in enumerate(zip(input_rows, classified)):
        row_num = i + 1
        for field in ('category', 'severity', 'reason'):
            if not str(cls.get(field, '')).strip():
                issues.append(f'Row {row_num}: missing field "{field}"')
        if cls.get('category') not in VALID_CATEGORIES:
            issues.append(
                f'Row {row_num}: invalid category "{cls.get("category")}"'
            )
        if cls.get('severity') not in VALID_SEVERITIES:
            issues.append(
                f'Row {row_num}: invalid severity "{cls.get("severity")}"'
            )
        # Severity blindness check
        complaint_text = str(orig.get('complaint', orig.get('text', orig.get('description', ''))))
        hint = fast_severity_hint(complaint_text)
        if hint == 'Critical' and cls.get('severity') in ('Low', 'Medium'):
            issues.append(
                f'Row {row_num}: severity "{cls.get("severity")}" may be too low '
                f'— safety keyword found in: "{complaint_text[:60]}..."'
            )
    return issues


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def detect_complaint_column(headers: list) -> str:
    """Find the column containing complaint text."""
    candidates = ['complaint', 'description', 'text', 'complaint_text',
                  'issue', 'details', 'message', 'content']
    headers_lower = [h.lower().strip() for h in headers]
    for candidate in candidates:
        if candidate in headers_lower:
            return headers[headers_lower.index(candidate)]
    # Fallback: return the last column (often the text field)
    return headers[-1]


def read_csv(filepath: str) -> tuple:
    """Returns (headers, rows) where rows is list of dicts."""
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        rows = list(reader)
    return list(headers), rows


def write_csv(filepath: str, headers: list, rows: list) -> None:
    output_headers = headers + ['category', 'severity', 'reason']
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=output_headers)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def classify_file(input_path: str) -> str:
    filename = os.path.basename(input_path)
    city = filename.replace('test_', '').replace('.csv', '')
    output_filename = f'results_{city}.csv'
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    print(f'\n{"="*60}')
    print(f'Processing: {filename}')
    print(f'City: {city}')
    print(f'{"="*60}')

    headers, rows = read_csv(input_path)
    complaint_col = detect_complaint_column(headers)
    print(f'Complaint column: "{complaint_col}"')
    print(f'Total complaints: {len(rows)}')

    # Extract complaint texts
    complaint_texts = [
        str(row.get(complaint_col, '')).strip() for row in rows
    ]

    # Classify in batches
    all_classified = []
    total_batches = (len(complaint_texts) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(total_batches):
        start = batch_idx * BATCH_SIZE
        end = start + BATCH_SIZE
        batch_texts = complaint_texts[start:end]

        print(f'\n  Batch {batch_idx+1}/{total_batches} '
              f'(rows {start+1}–{min(end, len(rows))})...')

        batch_results = classify_batch_with_retry(batch_texts)
        all_classified.extend(batch_results)

        # Small delay to avoid rate limiting
        if batch_idx < total_batches - 1:
            time.sleep(0.5)

    # Merge classified results back into original rows
    for i, (row, cls) in enumerate(zip(rows, all_classified)):
        row['category'] = cls.get('category', 'Other')
        row['severity'] = cls.get('severity', 'Medium')
        row['reason'] = cls.get('reason', '')

    # Save output CSV
    write_csv(output_path, headers, rows)
    print(f'\nSaved: {output_filename}')

    # CRAFT validation report
    print('\n--- CRAFT Validation Report ---')
    issues = validate_output(rows, all_classified)
    if issues:
        print(f'[WARN] {len(issues)} issue(s) found:')
        for issue in issues:
            print(f'  • {issue}')
        print('\nACTION: Review issues above. Refine prompt keywords and rerun.')
    else:
        print('[OK] All checks passed:')
        print(f'  ✓ Row count: {len(rows)} in → {len(rows)} out')
        print('  ✓ All categories valid')
        print('  ✓ All severities valid')
        print('  ✓ No severity blindness detected')

    # Summary statistics
    from collections import Counter
    cat_counts = Counter(row['category'] for row in rows)
    sev_counts = Counter(row['severity'] for row in rows)
    print('\n--- Classification Summary ---')
    print('By Category:')
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f'  {cat:<20} {count}')
    print('By Severity:')
    for sev in ['Critical', 'High', 'Medium', 'Low']:
        print(f'  {sev:<20} {sev_counts.get(sev, 0)}')

    return output_path


def find_city_files() -> list:
    """Auto-detect city test CSV files in data directory."""
    if not os.path.exists(DATA_DIR):
        return []
    return [
        os.path.join(DATA_DIR, f)
        for f in os.listdir(DATA_DIR)
        if f.startswith('test_') and f.endswith('.csv')
    ]


def main():
    print('UC-0A: Civic Complaint Classifier')
    print(f'Started: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

    # Determine which files to process
    if len(sys.argv) > 1:
        # Specific file(s) passed as arguments
        input_files = []
        for arg in sys.argv[1:]:
            if os.path.isabs(arg):
                input_files.append(arg)
            elif os.path.exists(arg):
                input_files.append(os.path.abspath(arg))
            else:
                # Try in data directory
                candidate = os.path.join(DATA_DIR, arg)
                if os.path.exists(candidate):
                    input_files.append(candidate)
                else:
                    print(f'[WARN] File not found: {arg}')
    else:
        # Auto-detect
        input_files = find_city_files()
        if not input_files:
            print(f'[ERROR] No test_*.csv files found in: {DATA_DIR}')
            print('Usage: python classifier.py test_pune.csv')
            sys.exit(1)

    print(f'Files to process: {len(input_files)}')

    for filepath in input_files:
        classify_file(filepath)

    print(f'\nDone. {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')


if __name__ == '__main__':
    main()
