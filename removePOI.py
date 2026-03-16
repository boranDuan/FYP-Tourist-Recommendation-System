import copy
import re


def enforce_remove_parse_rules(parsed, user_text):
    parsed = parsed if isinstance(parsed, dict) else {}
    intent = str(parsed.get("intent") or "").strip().lower()
    if intent != "remove_poi":
        return parsed

    txt = (user_text or "").lower()
    explicit_replace = bool(re.search(r"\b(replace|instead|swap|substitute)\b|换成|替换|改成|换一个", txt))
    if not explicit_replace and not parsed.get("needs_clarification"):
        parsed["needs_clarification"] = True
        parsed["clarification_type"] = "remove_or_replace"
        parsed["clarification_options"] = ["remove_only", "replace_nearby_same_type"]
        parsed["clarification_question"] = (
            "Do you want to remove it only, or remove and replace it with a similar nearby POI?"
        )
    return parsed


def resolve_choice_from_text(user_text, options, clarification_type=None):
    txt = str(user_text or "").strip().lower()
    if not txt:
        return None
    opts = [str(o or "").strip() for o in (options or []) if str(o or "").strip()]
    if clarification_type == "remove_or_replace":
        if "replace" in txt and "replace_nearby_same_type" in opts:
            return "replace_nearby_same_type"
        if any(k in txt for k in ("remove", "delete", "skip")) and "remove_only" in opts:
            return "remove_only"
    return None


def apply_choice_to_parsed(parsed, choice):
    out = copy.deepcopy(parsed or {})
    out["constraints"] = dict(out.get("constraints") or {})
    if choice == "remove_only":
        out["intent"] = "remove_poi"
    elif choice == "replace_nearby_same_type":
        out["intent"] = "replace_poi"
        out["constraints"]["nearby"] = True
    return out


def _remove_poi_from_plan(plan, poi_name, pick_poi_match, optimize_route_greedy_tsp, get_poi_duration):
    pois = list((plan or {}).get("pois") or [])
    idx, score = pick_poi_match(pois, poi_name)
    if idx < 0 or score <= 0:
        return None
    removed = pois.pop(idx)
    if len(pois) >= 2:
        pois = optimize_route_greedy_tsp(pois)
    plan["pois"] = pois
    plan["must_visit_count"] = sum(1 for p in pois if (p or {}).get("is_must_visit"))
    plan["total_hours"] = round(sum(get_poi_duration(p or {}) for p in pois), 1)
    return removed


def execute_remove_poi(
    *,
    parsed,
    poi_name,
    day,
    day_plans,
    content,
    trip,
    compute_candidates_and_itinerary,
    find_day_plan,
    pick_poi_match,
    optimize_route_greedy_tsp,
    get_poi_duration,
    refill_day_plan_from_pool,
    poi_uid,
):
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

    removed = _remove_poi_from_plan(source_plan, poi_name, pick_poi_match, optimize_route_greedy_tsp, get_poi_duration)
    if not removed:
        return {"kind": "error", "status": 404, "message": f"POI not found in target day: {poi_name}"}

    # Remove action should be pure deletion; do not auto-refill from candidate pool.
    added = []
    return {
        "kind": "applied",
        "removed": removed,
        "applied_day": source_plan.get("day"),
        "added": added,
    }
