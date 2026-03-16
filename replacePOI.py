def execute_replace_poi(
    *,
    parsed,
    day_plans,
    find_day_plan,
    pick_poi_match,
    recompute_plan_meta,
    replace_one_poi_in_plan,
    pool,
    pref,
    removed_poi_placeholder_name=None,
):
    constraints = parsed.get("constraints") if isinstance(parsed.get("constraints"), dict) else {}
    mode = str(constraints.get("mode") or "").strip().lower()

    # Guided swap mode: swap one existing POI with another existing POI.
    if mode == "swap_existing":
        source_day = parsed.get("day")
        source_name = str(parsed.get("poi_name") or "").strip()
        target_day = constraints.get("swap_with_day")
        target_name = str(constraints.get("swap_with_poi_name") or "").strip()
        try:
            source_day = int(source_day)
            target_day = int(target_day)
        except (TypeError, ValueError):
            return {"kind": "error", "status": 400, "message": "Source and target day are required for swap replace."}
        if not source_name or not target_name:
            return {"kind": "error", "status": 400, "message": "Source and target POI names are required for swap replace."}

        source_plan = find_day_plan(day_plans, source_day)
        target_plan = find_day_plan(day_plans, target_day)
        if not source_plan:
            return {"kind": "error", "status": 400, "message": f"Day {source_day} not found in current itinerary"}
        if not target_plan:
            return {"kind": "error", "status": 400, "message": f"Day {target_day} not found in current itinerary"}

        source_pois = list(source_plan.get("pois") or [])
        target_pois = list(target_plan.get("pois") or [])
        sidx, sscore = pick_poi_match(source_pois, source_name)
        tidx, tscore = pick_poi_match(target_pois, target_name)
        if sidx < 0 or sscore <= 0:
            return {"kind": "error", "status": 404, "message": f"POI not found in Day {source_day}: {source_name}"}
        if tidx < 0 or tscore <= 0:
            return {"kind": "error", "status": 404, "message": f"POI not found in Day {target_day}: {target_name}"}

        source_obj = source_pois[sidx]
        target_obj = target_pois[tidx]
        source_pois[sidx], target_pois[tidx] = target_obj, source_obj
        source_plan["pois"] = source_pois
        target_plan["pois"] = target_pois
        recompute_plan_meta(source_plan)
        if target_plan is not source_plan:
            recompute_plan_meta(target_plan)

        return {
            "kind": "applied",
            "removed": source_obj,
            "applied_day": source_day,
            "added": [target_obj],
            "target_day": target_day,
        }

    # Fallback legacy replace mode: replace removed POI using candidate pool.
    if not pool:
        return {"kind": "applied", "removed": None, "applied_day": parsed.get("day"), "added": []}
    return {"kind": "pass_through"}
