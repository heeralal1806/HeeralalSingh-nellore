# skills.md — UC-0A: Complaint Classifier

## Skill: classify_complaint

**Purpose:** Classify a single civic complaint into a category and severity level with a reason.

---

## Prompt Template

```
You are a civic complaint triage officer for a municipal corporation.

Classify the following complaint. Return ONLY a JSON object — no preamble, no explanation outside the JSON.

CLASSIFICATION RULES:

CATEGORIES (pick the most specific match):
- Roads: pothole, road, street, pavement, footpath, divider, traffic signal, speed breaker
- Water Supply: water, pipe, leakage, no water, tap, borewell, supply, drinking water
- Sanitation: garbage, waste, drain, sewage, stench, cleaning, sweeping, dustbin, litter
- Electricity: power, light, streetlight, electricity, transformer, wire, outage, blackout
- Public Safety: crime, harassment, theft, assault, danger, unsafe, attack, violence
- Health: hospital, disease, epidemic, mosquito, dengue, malaria, contamination, outbreak
- Trees & Parks: tree, park, garden, branch, fallen tree
- Noise: noise, sound, loudspeaker, construction noise, disturbance
- Other: only if no above category matches at all

SEVERITY RULES (Critical takes priority, check top-down):
- Critical: mentions injury, death, child, school, hospital, collapse, live wire, flood, outbreak, fire
- High: affects many people, main road blocked, major service disruption, risk of injury
- Medium: inconvenience to residents, partial disruption, non-urgent repair
- Low: minor aesthetic issue, isolated inconvenience, no safety risk

COMPLAINT:
"{complaint_text}"

Return this exact JSON structure:
{{
  "category": "<category>",
  "severity": "<Critical|High|Medium|Low>",
  "reason": "<one sentence explaining the classification>"
}}
```

---

## Skill: batch_classify

**Purpose:** Process an entire CSV of complaints efficiently using batch API calls.

```python
def batch_classify(complaints: list, batch_size: int = 10) -> list:
    """
    Classify complaints in batches to reduce API calls.
    Each batch sends up to batch_size complaints in one prompt.
    Returns list of dicts with category, severity, reason.
    """
    results = []
    for i in range(0, len(complaints), batch_size):
        batch = complaints[i:i + batch_size]
        results.extend(classify_batch(batch))
    return results
```

### Batch Prompt Template

```
You are a civic complaint triage officer. Classify each complaint below.

Return ONLY a JSON array — one object per complaint, in the same order.
No preamble. No markdown. Just the JSON array.

CATEGORIES: Roads | Water Supply | Sanitation | Electricity | Public Safety | Health | Trees & Parks | Noise | Other
SEVERITY: Critical (injury/child/school/hospital/collapse/flood/outbreak) | High (many affected/main road/major disruption) | Medium (inconvenience/partial) | Low (minor/aesthetic)

COMPLAINTS:
{numbered_complaints}

Return:
[
  {{"id": 1, "category": "...", "severity": "...", "reason": "..."}},
  {{"id": 2, "category": "...", "severity": "...", "reason": "..."}},
  ...
]
```

---

## Skill: validate_output

**Purpose:** Verify classifier output is complete and well-formed before saving.

```python
VALID_CATEGORIES = {
    'Roads', 'Water Supply', 'Sanitation', 'Electricity',
    'Public Safety', 'Health', 'Trees & Parks', 'Noise', 'Other'
}
VALID_SEVERITIES = {'Critical', 'High', 'Medium', 'Low'}

def validate_output(input_rows: list, output_rows: list) -> list:
    """
    Validates classifier output. Returns list of issues found.
    """
    issues = []

    # Row count check
    if len(input_rows) != len(output_rows):
        issues.append(
            f"Row count mismatch: input={len(input_rows)}, output={len(output_rows)}"
        )

    for i, row in enumerate(output_rows):
        # Required fields present
        for field in ('category', 'severity', 'reason'):
            if not row.get(field, '').strip():
                issues.append(f"Row {i+1}: missing '{field}'")

        # Valid category
        if row.get('category') not in VALID_CATEGORIES:
            issues.append(
                f"Row {i+1}: invalid category '{row.get('category')}'"
            )

        # Valid severity
        if row.get('severity') not in VALID_SEVERITIES:
            issues.append(
                f"Row {i+1}: invalid severity '{row.get('severity')}'"
            )

        # Severity blindness check — safety keywords should not be Low/Medium
        complaint = row.get('complaint_text', '').lower()
        severity = row.get('severity', '')
        safety_keywords = ['child', 'injur', 'hospital', 'school',
                           'collapse', 'live wire', 'flood', 'outbreak', 'death']
        if severity in ('Low', 'Medium'):
            for kw in safety_keywords:
                if kw in complaint:
                    issues.append(
                        f"Row {i+1}: severity '{severity}' too low — "
                        f"safety keyword '{kw}' found in complaint"
                    )
                    break

    return issues
```

---

## Skill: severity_keyword_check

**Purpose:** Pre-scan complaints for safety keywords before sending to API — allows fast-path Critical assignment.

```python
CRITICAL_KEYWORDS = [
    'child', 'children', 'school', 'hospital', 'injur', 'death', 'died',
    'collapse', 'collapsed', 'live wire', 'electrocut', 'flood', 'flooding',
    'outbreak', 'epidemic', 'fire', 'burning', 'accident'
]

HIGH_KEYWORDS = [
    'many', 'residents', 'entire colony', 'whole area', 'main road',
    'blocked', 'major', 'days', 'weeks', 'no water since', 'no power since'
]

def fast_severity_hint(complaint_text: str) -> str:
    """
    Returns a severity hint based on keyword scan.
    Used to double-check API output.
    """
    text = complaint_text.lower()
    for kw in CRITICAL_KEYWORDS:
        if kw in text:
            return 'Critical'
    for kw in HIGH_KEYWORDS:
        if kw in text:
            return 'High'
    return None   # No strong signal — let API decide
```

---

## CRAFT Loop for UC-0A

| Step | Action | Pass Condition |
|---|---|---|
| **C**reate | Run `classifier.py test_[city].csv` | `results_[city].csv` created |
| **R**ead | Open CSV, sort by severity, read "Other" rows | No obvious miscategorisations |
| **A**ssert | Run `validate_output()` | Zero issues returned |
| **F**ix | Add missing keywords to prompt, rerun | Issues list empty |
| **T**est | Run on second city file | Row count matches, no validation errors |

### Common Fixes After First Run

| Problem | Fix |
|---|---|
| Safety complaint marked Medium | Add keyword to `CRITICAL_KEYWORDS` list |
| Known category marked "Other" | Add keyword to category list in prompt |
| Reason field empty | Add enforcement line: "reason field is mandatory" |
| JSON parse error | Add fallback parser or retry logic |
