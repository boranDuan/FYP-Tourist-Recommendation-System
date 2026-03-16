def _extract_target_days(parsed):
    parsed = parsed if isinstance(parsed, dict) else {}
    constraints = parsed.get("constraints") if isinstance(parsed.get("constraints"), dict) else {}
    candidates = [
        constraints.get("day_count"),
        constraints.get("target_days"),
        parsed.get("target_day"),
        parsed.get("day"),
    ]
    for v in candidates:
        try:
            n = int(v)
            if n > 0:
                return n
        except (TypeError, ValueError):
            continue
    return None


def execute_adjust_day_plans(
    *,
    parsed,
    content,
    trip,
    compute_candidates_and_itinerary,
    daily_poi_cap_from_pref,
    recompute_plan_meta,
):
    day_count = _extract_target_days(parsed)
    if day_count is None:
        return {"kind": "error", "status": 400, "message": "Please provide target trip days (e.g., 3 days)."}
    if day_count < 1 or day_count > 14:
        return {"kind": "error", "status": 400, "message": "Trip days must be between 1 and 14."}

    pool = list(content.get("pois") or [])
    if not pool:
        pref = trip.preference
        generated = compute_candidates_and_itinerary(pref, limit=300) if pref else None
        pool = list((generated or {}).get("out") or [])

    cap = int(daily_poi_cap_from_pref(trip.preference))
    max_total = max(0, day_count * cap)
    selected = list(pool[:max_total])

    new_day_plans = [{"day": i + 1, "pois": []} for i in range(day_count)]
    for i, poi in enumerate(selected):
        day_idx = i % day_count
        if len(new_day_plans[day_idx]["pois"]) < cap:
            new_day_plans[day_idx]["pois"].append(poi)

    for dp in new_day_plans:
        recompute_plan_meta(dp)

    return {
        "kind": "applied",
        "day_plans": new_day_plans,
        "applied_day": 1,
        "added": [],
        "removed": None,
        "target_day": day_count,
    }
