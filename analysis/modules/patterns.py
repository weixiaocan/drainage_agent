from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

from .dry_curves import build_dry_curves


def _minute_label(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _analyze_period(values: np.ndarray, minutes: np.ndarray, start_min: int, end_min: int) -> dict[str, float]:
    if end_min == 24 * 60:
        mask = minutes >= start_min
    else:
        mask = (minutes >= start_min) & (minutes < end_min)
    period_values = values[mask]
    if len(period_values) == 0:
        return {"mean": 0.0, "max": 0.0, "min": 0.0}
    return {
        "mean": float(period_values.mean()),
        "max": float(period_values.max()),
        "min": float(period_values.min()),
    }


def _extract_hours(minutes: np.ndarray, mask: np.ndarray, min_hours: int) -> list[str]:
    hour_counts: dict[int, int] = {}
    for minute, matched in zip(minutes, mask):
        if matched:
            hour = int(minute) // 60
            hour_counts[hour] = hour_counts.get(hour, 0) + 1
    valid_hours = sorted(hour for hour, count in hour_counts.items() if count >= 30)
    if not valid_hours:
        return []

    periods: list[str] = []
    start = end = valid_hours[0]
    for hour in valid_hours[1:]:
        if hour == end + 1:
            end = hour
            continue
        if end - start + 1 >= min_hours:
            periods.append(f"{start}点" if start == end else f"{start}-{end}点")
        start = end = hour
    if end - start + 1 >= min_hours:
        periods.append(f"{start}点" if start == end else f"{start}-{end}点")
    return periods


def _extract_peak_valley_periods(curve: pd.DataFrame) -> tuple[list[str], list[str]]:
    flow = curve["flow_lps"].to_numpy()
    minutes = curve["minute_of_day"].to_numpy()
    max_val = float(flow.max()) if len(flow) else 0.0
    min_val = float(flow.min()) if len(flow) else 0.0
    if max_val - min_val < 0.1:
        return [], []
    threshold = (max_val + min_val) / 2
    peak_hours = _extract_hours(minutes, flow > threshold, min_hours=1)
    valley_hours = _extract_hours(minutes, flow <= threshold, min_hours=2)
    return peak_hours[:2], valley_hours[:2]


def _calculate_features(curve: pd.DataFrame) -> dict[str, object]:
    flow = curve["flow_lps"].to_numpy(dtype=float)
    minutes = curve["minute_of_day"].to_numpy(dtype=int)
    features: dict[str, object] = {
        "peak_value": float(flow.max()),
        "min_value": float(flow.min()),
        "mean_value": float(flow.mean()),
        "std_value": float(flow.std()),
    }
    min_value = float(features["min_value"])
    mean_value = float(features["mean_value"])
    features["peak_valley_ratio"] = float(features["peak_value"]) / min_value if min_value > 0.0001 else 99.9
    features["kz"] = float(features["peak_value"]) / mean_value if mean_value > 0.0001 else 0.0

    peaks, properties = find_peaks(flow, prominence=mean_value * 0.15, distance=120)
    prominences = properties.get("prominences", np.array([], dtype=float))
    features["peak_count"] = int(len(peaks))
    features["peak_times"] = [_minute_label(int(minutes[idx])) for idx in peaks]
    features["peak_significance"] = float(prominences.max() / mean_value) if len(prominences) and mean_value > 0 else 0.0
    features["morning_peak"] = _analyze_period(flow, minutes, 6 * 60, 11 * 60)
    features["evening_peak"] = _analyze_period(flow, minutes, 18 * 60, 24 * 60)
    features["night"] = _analyze_period(flow, minutes, 1 * 60, 5 * 60)
    features["daytime"] = _analyze_period(flow, minutes, 6 * 60, 24 * 60)
    daytime_mean = features["daytime"]["mean"]  # type: ignore[index]
    features["night_day_ratio"] = features["night"]["mean"] / daytime_mean if daytime_mean > 0 else 0.0  # type: ignore[index]
    return features


def _has_periodic_pattern(features: dict[str, object]) -> bool:
    peak_count = int(features.get("peak_count", 0))
    if peak_count < 4:
        return False
    mean_value = float(features.get("mean_value", 0.0))
    peak_value = float(features.get("peak_value", 0.0))
    min_value = float(features.get("min_value", 0.0))
    fluctuation = (peak_value - min_value) / mean_value if mean_value > 0 else 0.0
    return fluctuation > 0.2


def _looks_like_domestic_pattern(features: dict[str, object]) -> bool:
    mean_value = float(features.get("mean_value", 0.0))
    night = float(features["night"]["mean"])  # type: ignore[index]
    morning = float(features["morning_peak"]["mean"])  # type: ignore[index]
    evening = float(features["evening_peak"]["mean"])  # type: ignore[index]
    daytime = float(features["daytime"]["mean"])  # type: ignore[index]
    morning_max = float(features["morning_peak"]["max"])  # type: ignore[index]
    evening_max = float(features["evening_peak"]["max"])  # type: ignore[index]
    night_is_lowest = night < morning and night < evening and night < daytime
    evening_has_peak = (evening_max > mean_value * 1.15) or (night > 0 and evening > night * 1.15)
    morning_has_peak = morning_max > mean_value
    return night_is_lowest and evening_has_peak and morning_has_peak


def _classify_pattern(features: dict[str, object]) -> tuple[int, str]:
    kz = float(features.get("kz", 0.0))
    mean_value = float(features.get("mean_value", 0.0))
    if mean_value < 0.5:
        return 3, "流量接近零"
    if kz < 1.2:
        if _has_periodic_pattern(features):
            return 2, f"Kz={kz:.2f}<1.2但有周期性规律"
        return 3, f"Kz={kz:.2f}，曲线平坦"

    if _looks_like_domestic_pattern(features):
        return 1, f"Kz={kz:.2f}，有早晚高峰特征"
    if _has_periodic_pattern(features):
        return 2, f"一天内出现{int(features.get('peak_count', 0))}个波峰，呈锯齿状规律涨落，疑似泵站调控"
    return 2, f"Kz={kz:.2f}，波动特征不典型"


def _period_summary(features: dict[str, object]) -> str:
    labels = [
        ("夜间1:00-5:00", features["night"]["mean"]),  # type: ignore[index]
        ("早间6:00-11:00", features["morning_peak"]["mean"]),  # type: ignore[index]
        ("日间6:00-24:00", features["daytime"]["mean"]),  # type: ignore[index]
        ("晚间18:00-24:00", features["evening_peak"]["mean"]),  # type: ignore[index]
    ]
    if not any(float(value) > 0 for _, value in labels):
        return ""
    highest_label, highest_value = max(labels, key=lambda item: float(item[1]))
    lowest_label, lowest_value = min(labels, key=lambda item: float(item[1]))
    night = float(features["night"]["mean"])  # type: ignore[index]
    morning = float(features["morning_peak"]["mean"])  # type: ignore[index]
    evening = float(features["evening_peak"]["mean"])  # type: ignore[index]
    daytime = float(features["daytime"]["mean"])  # type: ignore[index]
    statements = [
        f"分时段均值显示，{highest_label}平均流量最高（{float(highest_value):.2f}L/s），{lowest_label}平均流量最低（{float(lowest_value):.2f}L/s）"
    ]
    if night < morning and night < evening and night < daytime:
        statements.append("夜间低流量特征较明显")
    else:
        statements.append("夜间流量未表现为全天最低，排放规律与典型居民生活污水存在差异")
    evening_peak_clear = daytime > 0 and evening > daytime * 1.1
    morning_peak_clear = daytime > 0 and morning > daytime * 1.1
    if evening_peak_clear:
        statements.append("晚间高峰较明显")
    elif morning_peak_clear:
        statements.append("早间高峰较明显")
    else:
        statements.append("早晚高峰不突出")
    return "，".join(statements)


def _fallback_pattern_sentence(category: int) -> str:
    if category == 1:
        return "曲线在日间或晚间用水时段出现抬升、夜间回落，整体符合生活污水排放规律"
    if category == 2:
        return "曲线存在明显波动，但高低峰出现时段与典型居民生活用水规律不完全一致，需结合汇水范围判断是否受工业排放、商业活动或泵站调度影响"
    if category == 3:
        return "流量曲线整体较为平坦，未形成清晰的早晚高峰特征，需关注持续入渗或恒定排放影响"
    return "流量曲线特征不明确，建议结合现场汇水范围进一步核查"


def _dedupe_text_parts(parts: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip().strip("。；;")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _build_description(
    point_id: str,
    features: dict[str, object],
    category: int,
    peak_periods: list[str],
    valley_periods: list[str],
    base_description: str = "",
) -> str:
    category_names = {
        1: "第1类，符合生活用水规律",
        2: "第2类，有波峰或波谷但不符合典型生活用水规律",
        3: "第3类，曲线平坦或无明显波峰波谷",
    }
    parts = [
        f"{point_id}点位属于{category_names.get(category, '未分类')}",
        f"时变化系数Kz为{float(features.get('kz', 0.0)):.2f}，识别到{int(features.get('peak_count', 0))}个波峰",
    ]
    peak_valley_ratio = float(features.get("peak_valley_ratio", 0.0))
    if peak_valley_ratio < 99:
        parts.append(f"峰谷比为{peak_valley_ratio:.2f}")
    summary = _period_summary(features)
    if summary:
        parts.append(summary)
    if peak_periods:
        parts.append(f"按小时阈值识别的相对高流量区间为{'、'.join(peak_periods)}")
    if valley_periods:
        parts.append(f"相对低流量区间为{'、'.join(valley_periods)}")
    if base_description:
        parts.append(base_description.rstrip("。"))
    else:
        parts.append(_fallback_pattern_sentence(category))
    return "。".join(_dedupe_text_parts(parts)) + "。"


def _description_from_llm_result(result: dict[str, object]) -> str:
    parts: list[str] = []
    for key in ("description", "curve_description", "short_conclusion", "cause_reasoning"):
        value = str(result.get(key) or "").strip()
        if value:
            parts.append(value)
    findings = result.get("key_findings") or []
    if isinstance(findings, list):
        for item in findings[:2]:
            text = str(item or "").strip()
            if text:
                parts.append(text)
    causes = result.get("possible_causes") or []
    if isinstance(causes, list) and causes:
        cause_text = "、".join(str(item).strip() for item in causes if str(item).strip())
        if cause_text:
            parts.append(f"可能原因包括{cause_text}")
    return "；".join(_dedupe_text_parts(parts))


def _analyze_with_llm(
    point_id: str,
    curve: pd.DataFrame,
    features: dict[str, object],
    peak_periods: list[str],
    valley_periods: list[str],
    llm_client,
) -> tuple[int, str]:
    flow = curve["flow_lps"].to_numpy(dtype=float)
    total_flow = float(flow.sum()) if float(flow.sum()) > 0 else 1.0

    halfhourly_avg = []
    for i in range(48):
        start = i * 30
        end = (i + 1) * 30
        halfhourly_avg.append(float(flow[start:end].mean()))
    halfhourly_avg_str = ", ".join(
        f"{i // 2}:{'00' if i % 2 == 0 else '30'}: {value:.2f}"
        for i, value in enumerate(halfhourly_avg)
    )

    night_flow = float(flow[0:420].mean())
    morning_flow = float(flow[420:600].mean())
    noon_flow = float(flow[660:900].mean())
    afternoon_flow = float(flow[900:1020].mean())
    evening_flow = float(flow[1080:1440].mean())

    night_ratio = float(flow[0:420].sum()) / total_flow
    morning_ratio = float(flow[420:600].sum()) / total_flow
    noon_ratio = float(flow[660:900].sum()) / total_flow
    afternoon_ratio = float(flow[900:1020].sum()) / total_flow
    evening_ratio = float(flow[1080:1440].sum()) / total_flow

    prompt_template = llm_client.load_prompt("pattern_analysis")
    prompt = prompt_template.format(
        point_name=point_id,
        mean_flow=float(features.get("mean_value", 0.0)),
        max_flow=float(features.get("peak_value", 0.0)),
        min_flow=float(features.get("min_value", 0.0)),
        kz=float(features.get("kz", 0.0)),
        peak_valley_ratio=float(features.get("peak_valley_ratio", 0.0)),
        peak_count=int(features.get("peak_count", 0)),
        peak_times="、".join(features.get("peak_times", [])),  # type: ignore[arg-type]
        peak_periods="、".join(peak_periods) if peak_periods else "无",
        valley_periods="、".join(valley_periods) if valley_periods else "无",
        halfhourly_avg=halfhourly_avg_str,
        night_avg=night_flow,
        morning_avg=morning_flow,
        noon_avg=noon_flow,
        afternoon_avg=afternoon_flow,
        evening_avg=evening_flow,
        night_ratio=night_ratio,
        morning_ratio=morning_ratio,
        noon_ratio=noon_ratio,
        afternoon_ratio=afternoon_ratio,
        evening_ratio=evening_ratio,
    )

    try:
        result = json.loads(llm_client.chat_json(prompt, temperature=0.1))
        category = int(result.get("category", 2))
        description = _description_from_llm_result(result)
        if category not in (1, 2, 3):
            category = 2

        kz = float(features.get("kz", 0.0))
        mean_val = float(features.get("mean_value", 0.0))
        max_val = float(features.get("peak_value", 0.0))
        min_val = float(features.get("min_value", 0.0))
        peak_count = int(features.get("peak_count", 0))
        peak_times = list(features.get("peak_times", []))  # type: ignore[arg-type]
        fluctuation = (max_val - min_val) / mean_val if mean_val > 0 else 0.0

        def has_periodic_pattern() -> bool:
            if peak_count < 4:
                return False
            night_peaks: list[str] = []
            for peak_time in peak_times:
                if "00:" <= peak_time <= "04:59":
                    hour, minute = int(peak_time[:2]), int(peak_time[3:5])
                    idx = hour * 60 + minute
                    if idx < len(curve) and float(curve["flow_lps"].iloc[idx]) > mean_val:
                        night_peaks.append(peak_time)
            return len(night_peaks) > 0

        periodic = has_periodic_pattern()

        if kz < 1.2:
            if periodic:
                category = 2
                description = f"Kz={kz:.2f}<1.2但有周期性规律，{description}"
            else:
                category = 3
                description = f"Kz={kz:.2f}<1.2，波动范围{fluctuation * 100:.0f}%<30%且波峰数{peak_count}<4，曲线平坦"
        elif periodic:
            if category != 2:
                category = 2
                description = f"一天内出现{peak_count}个波峰，呈锯齿状规律涨落，疑似泵站调控"
        else:
            night = float(features["night"]["mean"])  # type: ignore[index]
            morning = float(features["morning_peak"]["mean"])  # type: ignore[index]
            evening = float(features["evening_peak"]["mean"])  # type: ignore[index]
            daytime = float(features["daytime"]["mean"])  # type: ignore[index]
            evening_max = float(features["evening_peak"]["max"])  # type: ignore[index]
            morning_max = float(features["morning_peak"]["max"])  # type: ignore[index]
            night_is_lowest = night < morning and night < evening and night < daytime
            evening_has_peak = evening_max > mean_val * 1.15 if mean_val > 0 else False
            morning_has_peak = morning_max > mean_val if mean_val > 0 else False
            if night_is_lowest and evening_has_peak and morning_has_peak and category != 1:
                category = 1
                description = (
                    f"夜间流量最低，晚上高峰明显(峰值{evening_max:.1f}L/s为日均{mean_val:.1f}的"
                    f"{evening_max / mean_val:.1f}倍)，早上有小高峰，符合典型生活用水排放规律"
                )

        return category, _build_description(point_id, features, category, peak_periods, valley_periods, description)
    except Exception as exc:
        print(f"LLM分析失败({point_id}): {exc}，使用规则判断")
        category, _reason = _classify_pattern(features)
        description = _build_description(point_id, features, category, peak_periods, valley_periods)
        return category, description


def analyze_patterns(flow: pd.DataFrame, smooth_window_minutes: int = 20, llm_client=None) -> dict[str, object]:
    curves = build_dry_curves(flow, smooth_window_minutes=smooth_window_minutes)
    category_names = {
        1: "第1类-符合生活用水规律",
        2: "第2类-有波峰但不符合典型规律",
        3: "第3类-曲线平坦/异常",
    }
    rows = []
    descriptions: dict[str, str] = {}
    for point_id, curve in curves.items():
        features = _calculate_features(curve)
        peak_periods, valley_periods = _extract_peak_valley_periods(curve)
        if llm_client is not None:
            category, description = _analyze_with_llm(point_id, curve, features, peak_periods, valley_periods, llm_client)
        else:
            category, _reason = _classify_pattern(features)
            description = _build_description(point_id, features, category, peak_periods, valley_periods)
        reason = f"Kz={float(features['kz']):.2f}"
        rows.append(
            {
                "point_id": point_id,
                "category": category,
                "category_name": category_names.get(category, "未分类"),
                "kz": round(float(features["kz"]), 2),
                "peak_valley_ratio": round(float(features["peak_valley_ratio"]), 2)
                if float(features["peak_valley_ratio"]) < 99
                else "N/A",
                "peak_count": int(features["peak_count"]),
                "peak_periods": "、".join(peak_periods),
                "valley_periods": "、".join(valley_periods),
                "diagnosis_reason": reason,
                "description": description,
            }
        )
        descriptions[point_id] = description
    return {"patterns": pd.DataFrame(rows), "curves": curves, "descriptions": descriptions}
