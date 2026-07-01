"""Repair datasets whose horizontal_bar charts have swapped axes (category on x,
measure on y). Fixes the stored Step 7 spec and rebuilds the chart cache so both
the frontend dropdowns and the cached seed data are correct.

Safe to re-run (idempotent).
"""
import io
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

import pandas as pd
from api.supabase_client import get_supabase_client, download_file_bytes, update_dataset
from api.views import (
    _parse_json_if_string, CLEANED_DATA_BUCKET,
    _corrected_bar_axes, build_chart_cache,
)


def resolve_smart_path(dataset, gc):
    step6 = gc.get("step6") or {}
    smart_path = step6.get("output_path")
    if not smart_path:
        pp = dataset.get("processed_path", "")
        base = pp.rsplit(".", 1)[0] if pp else ""
        smart_path = base + "_smart.parquet" if base else None
    return smart_path


def main():
    sb = get_supabase_client()
    rows = sb.table("datasets").select("*").execute().data or []
    print(f"datasets: {len(rows)}")

    for d in rows:
        if d.get("status") != "completed":
            continue
        gc = _parse_json_if_string(d.get("global_context")) or {}
        step7 = gc.get("step7") or {}
        charts = step7.get("suggested_charts") or []
        hbars = [c for c in charts if str(c.get("chart_type")) == "horizontal_bar"]
        if not hbars:
            continue

        smart_path = resolve_smart_path(d, gc)
        try:
            fb = download_file_bytes(CLEANED_DATA_BUCKET, smart_path)
            df = pd.read_parquet(io.BytesIO(fb))
        except Exception as e:
            print(f"  [{d.get('id')}] skip (parquet load failed): {e}")
            continue

        changed = False
        for c in charts:
            if str(c.get("chart_type")) != "horizontal_bar":
                continue
            nx, ny = _corrected_bar_axes(df, c.get("x_axis"), c.get("y_axis"))
            if (nx, ny) != (c.get("x_axis"), c.get("y_axis")):
                print(f"  [{d.get('id')}] {d.get('file_name')} :: '{c.get('title')}' "
                      f"x/y {c.get('x_axis')}/{c.get('y_axis')} -> {nx}/{ny}")
                c["x_axis"] = nx
                c["y_axis"] = ny
                cols = c.get("columns")
                if isinstance(cols, list):
                    c["columns"] = [nx, ny]
                changed = True

        if not changed:
            continue

        # Rebuild cache with the corrected specs.
        gc["step7"]["suggested_charts"] = charts
        gc["chart_cache"] = build_chart_cache(df, charts)
        update_dataset(d.get("id"), {"global_context": gc})
        print(f"  [{d.get('id')}] updated spec + rebuilt cache "
              f"({gc['chart_cache'].get('chart_count')} charts)")

    print("done")


if __name__ == "__main__":
    main()
