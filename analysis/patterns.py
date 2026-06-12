from __future__ import annotations

import pandas as pd

from .dry_curves import build_dry_curves


def analyze_patterns(flow: pd.DataFrame, smooth_window_minutes: int = 20) -> dict[str, object]:
    curves = build_dry_curves(flow, smooth_window_minutes=smooth_window_minutes)
    rows = []
    for point_id, curve in curves.items():
        mean_flow = float(curve["flow_lps"].mean()) if not curve.empty else 0.0
        max_flow = float(curve["flow_lps"].max()) if not curve.empty else 0.0
        min_flow = float(curve["flow_lps"].min()) if not curve.empty else 0.0
        ratio = max_flow / min_flow if min_flow > 0 else 0.0
        category = "波动型" if ratio >= 2 else "平稳型"
        rows.append(
            {
                "point_id": point_id,
                "category": category,
                "kz": ratio,
                "peak_valley_ratio": ratio,
                "description": f"{point_id} 日内流量呈{category}，峰谷比 {ratio:.2f}。",
            }
        )
    return {"patterns": pd.DataFrame(rows), "curves": curves}

