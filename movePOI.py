def enforce_move_parse_rules(parsed):
    parsed = parsed if isinstance(parsed, dict) else {}
    intent = str(parsed.get("intent") or "").strip().lower()
    if intent != "move_poi":
        return parsed
    target_day = parsed.get("target_day")
    if target_day is None and not parsed.get("needs_clarification"):
        parsed["needs_clarification"] = True
        parsed["clarification_question"] = "Which target day do you want to move it to?"
    return parsed


def _pop_poi_from_plan(plan, poi_name, pick_poi_match, recompute_plan_meta):
    pois = list((plan or {}).get("pois") or [])
    idx, score = pick_poi_match(pois, poi_name)
    if idx < 0 or score <= 0:
        return None
    popped = pois.pop(idx)
    plan["pois"] = pois
    recompute_plan_meta(plan)
    return popped


def execute_move_poi(
    *,
    parsed,
    poi_name,
    day,
    target_day,
    day_plans,
    find_day_plan,
    pick_poi_match,
    recompute_plan_meta,
):
    def _cannot_move_last_poi(plan):
        pois = list((plan or {}).get("pois") or [])
        if len(pois) <= 1:
            d = (plan or {}).get("day")
            return {
                "kind": "error",
                "status": 400,
                "message": f"Day {d} has only one POI, so it cannot be moved.",
            }
        return None

    source_plan = None
    if day is not None:
        source_plan = find_day_plan(day_plans, day)
        if not source_plan:
            return {"kind": "error", "status": 400, "message": f"Day {day} not found in current itinerary"}
    else:
        strong_matches = []
        weak_matches = []
        for dp in day_plans:
            idx, score = pick_poi_match(dp.get("pois") or [], poi_name)
            if idx >= 0:
                if score >= 2:
                    strong_matches.append(dp)
                else:
                    weak_matches.append(dp)
        if len(strong_matches) == 0 and len(weak_matches) == 0:
            return {"kind": "error", "status": 404, "message": f"POI not found: {poi_name}"}
        if len(strong_matches) > 1:
            days = [m.get("day") for m in strong_matches]
            return {
                "kind": "clarify",
                "question": f"'{poi_name}' appears in multiple days {days}. Which day do you want to edit?",
                "parsed": parsed,
            }
        if len(strong_matches) == 1:
            source_plan = strong_matches[0]
        elif len(weak_matches) == 1:
            source_plan = weak_matches[0]
        else:
            days = [m.get("day") for m in weak_matches]
            return {
                "kind": "clarify",
                "question": f"I found multiple possible POIs matching '{poi_name}' in days {days}. Please tell me the day number or full POI name.",
                "parsed": parsed,
            }

    guard = _cannot_move_last_poi(source_plan)
    if guard:
        return guard

    if target_day is None:
        return {"kind": "clarify", "question": "Which target day do you want to move it to?", "parsed": parsed}
    target_plan = find_day_plan(day_plans, target_day)
    if not target_plan:
        return {"kind": "error", "status": 400, "message": f"Target day {target_day} not found"}
    if int(target_plan.get("day", 0)) == int(source_plan.get("day", -1)):
        return {"kind": "error", "status": 400, "message": "Source day and target day are the same"}

    moved = _pop_poi_from_plan(source_plan, poi_name, pick_poi_match, recompute_plan_meta)
    if not moved:
        return {"kind": "error", "status": 404, "message": f"POI not found in source day: {poi_name}"}

    # User-requested behavior: move should ignore daily cap constraints.
    target_pois = list(target_plan.get("pois") or [])
    target_pois.append(moved)
    target_plan["pois"] = target_pois
    recompute_plan_meta(target_plan)

    return {
        "kind": "applied",
        "removed": moved,
        "applied_day": source_plan.get("day"),
        "added": [moved],
        "target_day": target_plan.get("day"),
    }
