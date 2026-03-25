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


def _extract_target_day(parsed):
    parsed = parsed if isinstance(parsed, dict) else {}
    constraints = parsed.get("constraints") if isinstance(parsed.get("constraints"), dict) else {}
    candidates = [parsed.get("day"), parsed.get("target_day"), constraints.get("day")]
    for v in candidates:
        try:
            n = int(v)
            if n > 0:
                return n
        except (TypeError, ValueError):
            continue
    return None


def _extract_target_poi_count(parsed):
    parsed = parsed if isinstance(parsed, dict) else {}
    constraints = parsed.get("constraints") if isinstance(parsed.get("constraints"), dict) else {}
    candidates = [constraints.get("poi_count"), constraints.get("target_poi_count"), parsed.get("target_day")]
    for v in candidates:
        try:
            n = int(v)
            if n > 0:
                return n
        except (TypeError, ValueError):
            continue
    return None


def execute_adjust_poi_numbers(
    *,
    parsed,
    content,
    trip,
    compute_candidates_and_itinerary,
    recompute_plan_meta,
):
    day_plans = list(content.get("day_plans") or [])
    target_day = _extract_target_day(parsed)
    if target_day is None:
        return {"kind": "error", "status": 400, "message": "Please choose which day to adjust."}
    target_count = _extract_target_poi_count(parsed)
    if target_count is None:
        return {"kind": "error", "status": 400, "message": "Please provide target POI count for that day."}
    if target_count < 2:
        return {"kind": "error", "status": 400, "message": "Each day should include at least 2 POIs."}
    if target_count > 10:
        return {"kind": "error", "status": 400, "message": "Target POI count should be between 2 and 10."}

    target_plan = None
    for dp in day_plans:
        try:
            if int(dp.get("day")) == int(target_day):
                target_plan = dp
                break
        except (TypeError, ValueError):
            continue
    if not target_plan:
        return {"kind": "error", "status": 400, "message": f"Day {target_day} not found in current itinerary."}

    current = list(target_plan.get("pois") or [])
    if len(current) > target_count:
        current = current[:target_count]
        target_plan["pois"] = current
        recompute_plan_meta(target_plan)
        return {
            "kind": "applied",
            "day_plans": day_plans,
            "applied_day": int(target_day),
            "added": [],
            "removed": None,
            "target_day": int(target_day),
        }

    if len(current) == target_count:
        recompute_plan_meta(target_plan)
        return {
            "kind": "applied",
            "day_plans": day_plans,
            "applied_day": int(target_day),
            "added": [],
            "removed": None,
            "target_day": int(target_day),
        }

    pool = list(content.get("pois") or [])
    if not pool:
        pref = trip.preference
        generated = compute_candidates_and_itinerary(pref, limit=300) if pref else None
        pool = list((generated or {}).get("out") or [])

    existing_keys = set()
    for p in current:
        pid = str((p or {}).get("place_id") or "").strip()
        if pid:
            existing_keys.add("place:" + pid)
            continue
        poi_id = (p or {}).get("poi_id")
        if poi_id is not None:
            existing_keys.add("poi:" + str(poi_id))
            continue
        nm = str((p or {}).get("name") or "").strip().lower()
        if nm:
            existing_keys.add("name:" + nm)

    added = []
    for cand in pool:
        pid = str((cand or {}).get("place_id") or "").strip()
        if pid:
            key = "place:" + pid
        else:
            poi_id = (cand or {}).get("poi_id")
            if poi_id is not None:
                key = "poi:" + str(poi_id)
            else:
                key = "name:" + str((cand or {}).get("name") or "").strip().lower()
        if key in existing_keys:
            continue
        current.append(cand)
        existing_keys.add(key)
        added.append(cand)
        if len(current) >= target_count:
            break

    target_plan["pois"] = current
    recompute_plan_meta(target_plan)
    return {
        "kind": "applied",
        "day_plans": day_plans,
        "applied_day": int(target_day),
        "added": added,
        "removed": None,
        "target_day": int(target_day),
    }
