"""
Test script for Step 4: Technical Column Profiling.

Runs run_step4() directly against test_step4_data.csv and prints
a detailed report showing what was detected vs what we expect.

Usage:
    cd backend
    python -m tests.test_step4
"""

import sys
import os
import json

# Add backend to path so we can import the processing module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.processing.step4_column_detection import run_step4


# ══════════════════════════════════════════════════════════════
# EXPECTED RESULTS — what Step 4 SHOULD detect for each column
# ══════════════════════════════════════════════════════════════
EXPECTED = {
    # Column name         -> (expected_clean_name, expected_type, challenge_description)
    "Order ID":           ("order_id",        "numeric",   "Integers that are IDs, not metrics"),
    "Customer Phone":     ("customer_phone",  "text",      "Looks numeric but has +966 prefix"),
    "Zip Code":           ("zip_code",        "text",      "Leading zeros (00123) and mixed formats (W1A 0AX)"),
    "Total (Price)":      ("total_price",     "numeric",   "Parentheses in name + has negative value (-150)"),
    "Discount %":         ("discount",        "numeric",   "Special char in name (%) + has nulls"),
    "Business Date":      ("business_date",   "datetime",  "ISO dates as text strings"),
    "created_at":         ("created_at",      "datetime",  "Datetime with time component"),
    "  Shipped At  ":     ("shipped_at",      "datetime",  "Leading/trailing spaces in name + many nulls"),
    "cancelled_at":       ("cancelled_at",    "datetime",  "ALL values are null, name hints datetime"),
    "Status":             ("status",          "text",      "Low-cardinality categorical text"),
    "Branch Name":        ("branch_name",     "text",      "Categorical with spaces"),
    "is_active":          ("is_active",       "boolean",   "Boolean as true/false strings"),
    "Notes / Comments":   ("notes_comments",  "text",      "Slash in name + long text with commas"),
    "Empty Column":       ("empty_column",    "text",      "100% null column — total unknown"),
    "Tags":               ("tags",            "text",      "Semicolon-separated values"),
    "quantity":            ("quantity",        "numeric",   "Clean integers — should be easy"),
    "Rating (1-5)":       ("rating_1_5",      "numeric",   "Decimals with nulls + special chars in name"),
    "Latitude":           ("latitude",        "numeric",   "Float coordinates — tricky: looks like numbers"),
    "product_code":       ("product_code",    "text",      "Alphanumeric codes (PRD-001)"),
}


def main():
    # ── Load test file ────────────────────────────────────────
    csv_path = os.path.join(os.path.dirname(__file__), "test_step4_data.csv")

    if not os.path.exists(csv_path):
        print(f"ERROR: Test file not found at {csv_path}")
        sys.exit(1)

    with open(csv_path, "rb") as f:
        file_bytes = f.read()

    print("=" * 70)
    print("  STEP 4 TEST: Technical Column Profiling")
    print("=" * 70)
    print(f"\nTest file: {csv_path}")
    print(f"File size: {len(file_bytes):,} bytes\n")

    # ── Run Step 4 ────────────────────────────────────────────
    try:
        result = run_step4(file_bytes, "test_step4_data.csv")
    except Exception as e:
        print(f"FATAL ERROR: run_step4 crashed!\n{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # ── Print file info ───────────────────────────────────────
    fi = result["file_info"]
    print("-" * 70)
    print("  FILE INFO")
    print("-" * 70)
    print(f"  Rows:        {fi['row_count']}")
    print(f"  Columns:     {fi['column_count']}")
    print(f"  Size:        {fi['file_size_bytes']:,} bytes")
    print(f"  Encoding:    {fi['encoding']}")
    print()

    # ── Check each column ─────────────────────────────────────
    print("-" * 70)
    print("  COLUMN-BY-COLUMN RESULTS")
    print("-" * 70)

    passed = 0
    failed = 0
    warnings = 0
    columns = result["columns"]

    for col in columns:
        orig = col["original_name"]
        clean = col["clean_name"]
        dtype = col["data_type"]
        stats = col["technical_stats"]

        exp = EXPECTED.get(orig)

        if exp:
            exp_clean, exp_type, challenge = exp
            name_ok = (clean == exp_clean)
            type_ok = (dtype == exp_type)

            if name_ok and type_ok:
                status_icon = "PASS"
                passed += 1
            elif type_ok and not name_ok:
                status_icon = "WARN"  # Type correct but name differs
                warnings += 1
            else:
                status_icon = "FAIL"
                failed += 1
        else:
            status_icon = "????"
            exp_clean = "?"
            exp_type = "?"
            challenge = "Unknown column — not in expected list"
            warnings += 1

        print(f"\n  [{status_icon}] \"{orig}\"")
        print(f"         Challenge:  {challenge}")
        print(f"         Clean name: {clean}", end="")
        if exp and not (clean == exp_clean):
            print(f"  (expected: {exp_clean})", end="")
        print()
        print(f"         Type:       {dtype}", end="")
        if exp and not (dtype == exp_type):
            print(f"  << EXPECTED: {exp_type} >>", end="")
        print()

        # Print key stats
        print(f"         Null ratio: {stats.get('null_ratio', '?')}")
        print(f"         Unique:     {stats.get('unique_ratio', '?')}")
        samples = stats.get("top_5_samples", [])
        if samples:
            sample_str = ", ".join(str(s) for s in samples[:3])
            print(f"         Samples:    [{sample_str}, ...]")

        # Type-specific stats
        if dtype == "numeric":
            print(f"         Range:      {stats.get('min')} .. {stats.get('max')}")
            print(f"         Mean:       {stats.get('mean')}  StdDev: {stats.get('std_dev')}")
        elif dtype == "datetime":
            print(f"         Date range: {stats.get('min_date')} .. {stats.get('max_date')}")
        elif dtype == "text":
            print(f"         Avg length: {stats.get('avg_char_length')} chars")

    # ── Summary ───────────────────────────────────────────────
    total = passed + failed + warnings
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  Total columns:  {len(columns)}")
    print(f"  PASSED:         {passed}")
    print(f"  FAILED:         {failed}")
    print(f"  WARNINGS:       {warnings}")
    print()

    if failed == 0:
        print("  All type detections matched expectations!")
    else:
        print(f"  {failed} column(s) had unexpected type detection.")
        print("  Review the FAIL items above to decide if the logic needs adjusting.")

    # ── Dump full JSON for inspection ─────────────────────────
    print("\n" + "-" * 70)
    print("  FULL JSON OUTPUT (for manual inspection)")
    print("-" * 70)
    print(json.dumps(result, indent=2, default=str))

    return failed


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
