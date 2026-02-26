import copy
import json
import os
import re

from flask import jsonify, request, session


_CTX = {}


def init_change_poi(
    *,
    db,
    Trip,
    Itinerary,
    OpenAI,
    optimize_route_greedy_tsp,
    get_poi_duration,
    format_itinerary_summary,
    PACE_TO_DAILY_POI_CAP,
    INTEREST_TO_FILTER_IDS,
    DUBLIN_CENTER,
    haversine_km,
    is_same_location,
    compute_candidates_and_itinerary,
    persist_itinerary_if_missing,
    get_trip_days,
):
    _CTX.clear()
    _CTX.update(
        {
            "db": db,
            "Trip": Trip,
            "Itinerary": Itinerary,
            "OpenAI": OpenAI,
            "optimize_route_greedy_tsp": optimize_route_greedy_tsp,
            "get_poi_duration": get_poi_duration,
            "format_itinerary_summary": format_itinerary_summary,
            "PACE_TO_DAILY_POI_CAP": PACE_TO_DAILY_POI_CAP,
            "INTEREST_TO_FILTER_IDS": INTEREST_TO_FILTER_IDS,
            "DUBLIN_CENTER": DUBLIN_CENTER,
            "haversine_km": haversine_km,
            "is_same_location": is_same_location,
            "compute_candidates_and_itinerary": compute_candidates_and_itinerary,
            "persist_itinerary_if_missing": persist_itinerary_if_missing,
            "get_trip_days": get_trip_days,
        }
    )


def _ctx(key):
    if key not in _CTX:
        raise RuntimeError(f"changePOI is not initialized: missing '{key}'")
    return _CTX[key]


def _resp_error(message, status=400, **extra):
    payload = {"success": False, "message": message}
    payload.update(extra)
    return jsonify(payload), status


def _resp_parsed(trip_id, parsed):
    return jsonify(
        {
            "success": True,
            "trip_id": trip_id,
            "parsed": parsed,
            "message": "Parsed only. No itinerary change applied.",
        }
    ), 200


def _resp_clarify(trip_id, parsed, question, clarification_type=None, clarification_options=None):
    return jsonify(
        {
            "success": True,
            "applied": False,
            "trip_id": trip_id,
            "needs_clarification": True,
            "clarification_question": question or "Could you clarify your request?",
            "clarification_type": clarification_type,
            "clarification_options": clarification_options,
            "parsed": parsed,
        }
    ), 200


def _resp_applied(trip_id, new_version, applied_action, day_plans, summary, parsed):
    return jsonify(
        {
            "success": True,
            "applied": True,
            "trip_id": trip_id,
            "new_version": new_version,
            "applied_action": applied_action,
            "day_plans": day_plans,
            "summary": summary,
            "parsed": parsed,
        }
    ), 200


def _extract_json_object(text):
    s = (text or "").strip()
    if not s:
        return None
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", s)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _llm_parse_itinerary_edit(user_text):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, "AI parser is not configured (missing OPENAI_API_KEY)"

    OpenAI = _ctx("OpenAI")
    if OpenAI is None:
        return None, "OpenAI client not installed"

    parser_system = """You are an itinerary edit intent parser.
Convert user request into ONE strict JSON object only (no prose).

Allowed intents:
- remove_poi
- add_poi
- replace_poi
- move_poi
- swap_poi
- reroute_day
- unknown

Output schema (always include all keys):
{
  "intent": "remove_poi|add_poi|replace_poi|move_poi|swap_poi|reroute_day|unknown",
  "day": <number|null>,
  "poi_name": <string|null>,
  "target_day": <number|null>,
  "constraints": {
    "nearby": <boolean>,
    "same_type": <string|null>,
    "avoid_type": <string|null>
  },
  "confidence": <number 0.0-1.0>,
  "needs_clarification": <boolean>,
  "clarification_question": <string|null>
}

Rules:
- If ambiguous, set needs_clarification=true and ask one short question.
- Do not invent POI names.
- Use null when value is not provided.
"""
    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": parser_system},
                {"role": "user", "content": user_text or ""},
            ],
            max_tokens=320,
        )
        raw = (resp.choices[0].message.content or "").strip()
        parsed = _extract_json_object(raw)
        if not parsed:
            return None, "Failed to parse JSON from LLM response"

        parsed.setdefault("intent", "unknown")
        parsed.setdefault("day", None)
        parsed.setdefault("poi_name", None)
        parsed.setdefault("target_day", None)
        parsed.setdefault("constraints", {})
        parsed.setdefault("confidence", 0.0)
        parsed.setdefault("needs_clarification", parsed.get("intent") == "unknown")
        parsed.setdefault("clarification_question", None)
        parsed.setdefault("clarification_type", None)
        parsed.setdefault("clarification_options", None)

        intent = (parsed.get("intent") or "").strip()
        txt = (user_text or "").lower()
        explicit_replace = bool(re.search(r"\b(replace|instead|swap|substitute)\b|换成|替换|改成|换一个", txt))
        if intent == "remove_poi" and not explicit_replace and not parsed.get("needs_clarification"):
            parsed["needs_clarification"] = True
            parsed["clarification_type"] = "remove_or_replace"
            parsed["clarification_options"] = ["remove_only", "replace_nearby_same_type"]
            parsed["clarification_question"] = (
                "Do you want to remove it only, or remove and replace it with a similar nearby POI?"
            )
        return parsed, None
    except Exception as e:
        return None, str(e)


def _norm_poi_name(name):
    s = str(name or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _find_day_plan(day_plans, day_num):
    for plan in (day_plans or []):
        if number := plan.get("day"):
            try:
                if int(number) == int(day_num):
                    return plan
            except Exception:
                continue
    return None


def _pick_poi_index_by_name(pois, target_name):
    target = _norm_poi_name(target_name)
    if not target:
        return -1
    best_idx, best_score = _pick_poi_match(pois, target)
    return best_idx if best_score > 0 else -1


def _pick_poi_match(pois, target_name):
    target = _norm_poi_name(target_name)
    if not target:
        return -1, -1
    target_tokens = [t for t in target.split(" ") if t]
    best_idx = -1
    best_score = -1
    for i, p in enumerate(pois or []):
        cur = _norm_poi_name((p or {}).get("name"))
        if not cur:
            continue
        score = 0
        if cur == target:
            score = 3
        elif target in cur or cur in target:
            if len(target_tokens) <= 1 or len(target) < 6:
                score = 1
            else:
                score = 2
        elif any(tok and tok in cur for tok in target.split(" ")):
            score = 1
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx, best_score


def _remove_poi_from_plan(plan, poi_name):
    pois = list(plan.get("pois") or [])
    idx = _pick_poi_index_by_name(pois, poi_name)
    if idx < 0:
        return None
    removed = pois.pop(idx)
    optimize_route_greedy_tsp = _ctx("optimize_route_greedy_tsp")
    get_poi_duration = _ctx("get_poi_duration")
    if len(pois) >= 2:
        pois = optimize_route_greedy_tsp(pois)
    plan["pois"] = pois
    plan["must_visit_count"] = sum(1 for p in pois if (p or {}).get("is_must_visit"))
    plan["total_hours"] = round(sum(get_poi_duration(p or {}) for p in pois), 1)
    return removed


def _pop_poi_from_plan(plan, poi_name):
    pois = list(plan.get("pois") or [])
    idx = _pick_poi_index_by_name(pois, poi_name)
    if idx < 0:
        return None
    popped = pois.pop(idx)
    plan["pois"] = pois
    _recompute_plan_meta(plan)
    return popped


def _append_poi_to_plan_with_constraints(target_plan, poi, pref):
    if not isinstance(target_plan, dict) or not isinstance(poi, dict):
        return False, "invalid input"
    pois = list(target_plan.get("pois") or [])
    cap = _daily_poi_cap_from_pref(pref)
    if len(pois) >= cap:
        return False, f"target day already reached cap ({cap})"

    get_poi_duration = _ctx("get_poi_duration")
    pace = ((getattr(pref, "pace", None) or "balanced") if pref else "balanced").lower()
    daily_hours_map = {"relaxed": 5.0, "balanced": 6.5, "intensive": 8.0}
    daily_hours = float(daily_hours_map.get(pace, 6.5))
    soft_day_hour_cap = max(daily_hours, cap * 2.5)
    day_hours = sum(get_poi_duration(p or {}) for p in pois)
    add_hours = get_poi_duration(poi or {})
    if day_hours + add_hours > soft_day_hour_cap:
        return False, "target day soft hour limit exceeded"

    pois.append(poi)
    target_plan["pois"] = pois
    _recompute_plan_meta(target_plan)
    return True, None


def _save_new_itinerary_version(trip_id, content_json):
    Itinerary = _ctx("Itinerary")
    db = _ctx("db")
    active = (
        Itinerary.query
        .filter_by(trip_id=trip_id, is_active=True)
        .order_by(Itinerary.version.desc())
        .first()
    )
    latest = (
        Itinerary.query
        .filter_by(trip_id=trip_id)
        .order_by(Itinerary.version.desc())
        .first()
    )
    next_version = (latest.version + 1) if latest else 1
    if active:
        active.is_active = False

    row = Itinerary(
        trip_id=trip_id,
        version=next_version,
        content_json=content_json,
        is_active=True,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _poi_uid(poi):
    if not isinstance(poi, dict):
        return None
    return poi.get("place_id") or poi.get("poi_id")


def _plan_center_from_pois(pois):
    dublin_center = _ctx("DUBLIN_CENTER")
    lats = []
    lngs = []
    for p in pois or []:
        la = (p or {}).get("latitude")
        lo = (p or {}).get("longitude")
        if la is None or lo is None:
            continue
        try:
            lats.append(float(la))
            lngs.append(float(lo))
        except (TypeError, ValueError):
            continue
    if not lats:
        return dublin_center
    return (sum(lats) / len(lats), sum(lngs) / len(lngs))


def _minimum_daily_target(pref):
    pace = ((getattr(pref, "pace", None) or "balanced") if pref else "balanced").lower()
    if pace == "balanced":
        return 3
    return 0


def _daily_poi_cap_from_pref(pref):
    pace_to_daily_poi_cap = _ctx("PACE_TO_DAILY_POI_CAP")
    pace = ((getattr(pref, "pace", None) or "balanced") if pref else "balanced").lower()
    return int(pace_to_daily_poi_cap.get(pace, 4))


def _recompute_plan_meta(plan):
    optimize_route_greedy_tsp = _ctx("optimize_route_greedy_tsp")
    get_poi_duration = _ctx("get_poi_duration")
    pois = list((plan or {}).get("pois") or [])
    if len(pois) >= 2:
        pois = optimize_route_greedy_tsp(pois)
    plan["pois"] = pois
    plan["must_visit_count"] = sum(1 for p in pois if (p or {}).get("is_must_visit"))
    plan["total_hours"] = round(sum(get_poi_duration(p or {}) for p in pois), 1)


def _candidate_rank_for_refill(candidate, center):
    haversine_km = _ctx("haversine_km")
    score = _safe_float((candidate or {}).get("score"), 0.0)
    la = _safe_float((candidate or {}).get("latitude"), None)
    lo = _safe_float((candidate or {}).get("longitude"), None)
    if la is None or lo is None:
        return score * 0.7
    dist_km = haversine_km(float(la), float(lo), float(center[0]), float(center[1]))
    dist_fit = max(0.0, 1.0 - min(10.0, dist_km) / 10.0)
    return score * 0.7 + dist_fit * 0.3


def _refill_day_plan_from_pool(plan, day_plans, pool, pref, banned_uids=None, dedup_km=0.35):
    haversine_km = _ctx("haversine_km")
    is_same_location = _ctx("is_same_location")
    dublin_center = _ctx("DUBLIN_CENTER")
    optimize_route_greedy_tsp = _ctx("optimize_route_greedy_tsp")
    get_poi_duration = _ctx("get_poi_duration")

    banned_uids = set(banned_uids or [])
    min_target = _minimum_daily_target(pref)
    if min_target <= 0:
        return []

    day_pois = list(plan.get("pois") or [])
    if len(day_pois) >= min_target:
        return []

    center = _plan_center_from_pois(day_pois)
    used_uids = set()
    all_existing = []
    for dp in day_plans or []:
        for p in (dp.get("pois") or []):
            all_existing.append(p)
            uid = _poi_uid(p)
            if uid:
                used_uids.add(uid)

    added = []
    while len(day_pois) < min_target:
        candidates = []
        for c in pool or []:
            uid = _poi_uid(c)
            if uid and (uid in used_uids or uid in banned_uids):
                continue
            la = (c or {}).get("latitude")
            lo = (c or {}).get("longitude")
            if la is None or lo is None:
                continue
            try:
                city_dist = haversine_km(float(la), float(lo), float(dublin_center[0]), float(dublin_center[1]))
                day_dist = haversine_km(float(la), float(lo), float(center[0]), float(center[1]))
            except (TypeError, ValueError):
                continue
            if city_dist > 8.0:
                continue
            if day_dist > 10.0:
                continue
            if any(is_same_location(c, ep, threshold_km=dedup_km) for ep in all_existing):
                continue
            candidates.append(c)

        if not candidates:
            break
        best = max(candidates, key=lambda x: _candidate_rank_for_refill(x, center))
        day_pois.append(best)
        added.append(best)
        uid = _poi_uid(best)
        if uid:
            used_uids.add(uid)
        all_existing.append(best)
        center = _plan_center_from_pois(day_pois)

    if len(day_pois) >= 2:
        day_pois = optimize_route_greedy_tsp(day_pois)
    plan["pois"] = day_pois
    plan["must_visit_count"] = sum(1 for p in day_pois if (p or {}).get("is_must_visit"))
    plan["total_hours"] = round(sum(get_poi_duration(p or {}) for p in day_pois), 1)
    return added


def _same_type_filter_ids_from_constraints(constraints):
    interest_to_filter_ids = _ctx("INTEREST_TO_FILTER_IDS")
    if not isinstance(constraints, dict):
        return set()
    t = str(constraints.get("same_type") or "").strip().lower()
    if not t:
        return set()
    mapping = {
        "museum": set(interest_to_filter_ids.get("museum", [])),
        "nature": set(interest_to_filter_ids.get("nature", [])),
        "culture": set(interest_to_filter_ids.get("culture", [])),
        "shopping": set(interest_to_filter_ids.get("shopping", [])),
    }
    return mapping.get(t, set())


def _related_type_filter_ids(same_type):
    interest_to_filter_ids = _ctx("INTEREST_TO_FILTER_IDS")
    t = str(same_type or "").strip().lower()
    if not t:
        return set()
    if t == "museum":
        keys = ["museum", "culture"]
    elif t == "culture":
        keys = ["culture", "museum"]
    elif t == "nature":
        keys = ["nature", "culture"]
    elif t == "shopping":
        keys = ["shopping", "culture"]
    else:
        keys = [t]
    out = set()
    for k in keys:
        out |= set(interest_to_filter_ids.get(k, []))
    return out


def _replace_one_poi_in_plan(plan, day_plans, pool, pref, removed_poi, constraints=None, dedup_km=0.35):
    haversine_km = _ctx("haversine_km")
    is_same_location = _ctx("is_same_location")
    dublin_center = _ctx("DUBLIN_CENTER")
    optimize_route_greedy_tsp = _ctx("optimize_route_greedy_tsp")
    get_poi_duration = _ctx("get_poi_duration")

    constraints = constraints or {}
    same_type = str((constraints or {}).get("same_type") or "").strip().lower()
    same_type_fids = _same_type_filter_ids_from_constraints(constraints)
    related_type_fids = _related_type_filter_ids(same_type)

    day_pois = list(plan.get("pois") or [])
    center = _plan_center_from_pois(day_pois)

    used_uids = set()
    all_existing = []
    for dp in day_plans or []:
        for p in (dp.get("pois") or []):
            all_existing.append(p)
            uid = _poi_uid(p)
            if uid:
                used_uids.add(uid)

    banned = {_poi_uid(removed_poi)} if isinstance(removed_poi, dict) else set()

    def _collect_candidates(max_city_km, max_day_km, type_fids=None):
        candidates = []
        for c in pool or []:
            uid = _poi_uid(c)
            if uid and (uid in used_uids or uid in banned):
                continue
            la = (c or {}).get("latitude")
            lo = (c or {}).get("longitude")
            if la is None or lo is None:
                continue
            try:
                city_dist = haversine_km(float(la), float(lo), float(dublin_center[0]), float(dublin_center[1]))
                day_dist = haversine_km(float(la), float(lo), float(center[0]), float(center[1]))
            except (TypeError, ValueError):
                continue
            if city_dist > max_city_km or day_dist > max_day_km:
                continue
            if any(is_same_location(c, ep, threshold_km=dedup_km) for ep in all_existing):
                continue
            if type_fids:
                c_fids = set((c or {}).get("filter_ids") or [])
                if not (c_fids & type_fids):
                    continue
            candidates.append(c)
        return candidates

    fallback_levels = []
    if same_type_fids:
        fallback_levels.append((8.0, 10.0, same_type_fids))
        fallback_levels.append((10.0, 14.0, same_type_fids))
    if related_type_fids and related_type_fids != same_type_fids:
        fallback_levels.append((10.0, 14.0, related_type_fids))
    fallback_levels.append((12.0, 18.0, set()))
    fallback_levels.append((20.0, 30.0, set()))

    best = None
    for max_city_km, max_day_km, type_fids in fallback_levels:
        candidates = _collect_candidates(max_city_km, max_day_km, type_fids)
        if not candidates:
            continue
        best = max(candidates, key=lambda x: _candidate_rank_for_refill(x, center))
        break
    if not best:
        return None

    day_pois.append(best)
    if len(day_pois) >= 2:
        day_pois = optimize_route_greedy_tsp(day_pois)
    plan["pois"] = day_pois
    plan["must_visit_count"] = sum(1 for p in day_pois if (p or {}).get("is_must_visit"))
    plan["total_hours"] = round(sum(get_poi_duration(p or {}) for p in day_pois), 1)
    return best


def register_change_poi_routes(app):
    @app.route("/api/trips/<int:trip_id>/itinerary/parse-edit", methods=["POST"])
    def parse_itinerary_edit(trip_id):
        user_id = session.get("user_id")
        if not user_id:
            return _resp_error("Please log in first.", status=401)

        db = _ctx("db")
        Trip = _ctx("Trip")
        trip = db.session.get(Trip, trip_id)
        if not trip:
            return _resp_error("Trip not found.", status=404)
        if trip.user_id != user_id:
            return _resp_error("You do not have access to this trip.", status=403)

        data = request.get_json() or {}
        user_text = (data.get("user_text") or "").strip()
        if not user_text:
            return _resp_error("user_text is required", status=400)

        parsed, err = _llm_parse_itinerary_edit(user_text)
        if err:
            return _resp_error(err, status=500)

        return _resp_parsed(trip_id, parsed)

    @app.route("/api/trips/<int:trip_id>/itinerary/apply-edit", methods=["POST"])
    def apply_itinerary_edit(trip_id):
        user_id = session.get("user_id")
        if not user_id:
            return _resp_error("Please log in first.", status=401)

        db = _ctx("db")
        Trip = _ctx("Trip")
        Itinerary = _ctx("Itinerary")
        compute_candidates_and_itinerary = _ctx("compute_candidates_and_itinerary")
        persist_itinerary_if_missing = _ctx("persist_itinerary_if_missing")
        get_trip_days = _ctx("get_trip_days")
        format_itinerary_summary = _ctx("format_itinerary_summary")

        trip = db.session.get(Trip, trip_id)
        if not trip:
            return _resp_error("Trip not found.", status=404)
        if trip.user_id != user_id:
            return _resp_error("You do not have access to this trip.", status=403)

        data = request.get_json() or {}
        parsed = data.get("parsed") if isinstance(data.get("parsed"), dict) else None
        if not parsed:
            user_text = (data.get("user_text") or "").strip()
            if not user_text:
                return _resp_error("parsed or user_text is required", status=400)
            parsed, err = _llm_parse_itinerary_edit(user_text)
            if err:
                return _resp_error(err, status=500)

        if parsed.get("needs_clarification"):
            return _resp_clarify(
                trip_id,
                parsed,
                parsed.get("clarification_question"),
                clarification_type=parsed.get("clarification_type"),
                clarification_options=parsed.get("clarification_options"),
            )

        intent = (parsed.get("intent") or "").strip()
        if intent not in ("remove_poi", "replace_poi", "move_poi"):
            return _resp_error(
                f"Unsupported intent for MVP apply-edit: {intent or 'unknown'} (currently supports remove_poi/replace_poi/move_poi)",
                status=400,
                parsed=parsed,
            )

        day = parsed.get("day")
        poi_name = (parsed.get("poi_name") or "").strip()
        if not poi_name:
            return _resp_error(
                "poi_name is required for remove_poi/replace_poi/move_poi",
                status=400,
                parsed=parsed,
            )

        active = (
            Itinerary.query
            .filter_by(trip_id=trip_id, is_active=True)
            .order_by(Itinerary.version.desc())
            .first()
        )
        if not active:
            pref = trip.preference
            if not pref:
                return _resp_error("Trip has no preference", status=400)
            generated = compute_candidates_and_itinerary(pref)
            if not generated:
                return _resp_error("No itinerary generated for this trip", status=400)
            persist_itinerary_if_missing(trip_id, generated)
            active = (
                Itinerary.query
                .filter_by(trip_id=trip_id, is_active=True)
                .order_by(Itinerary.version.desc())
                .first()
            )
            if not active:
                return _resp_error("Failed to initialize active itinerary", status=500)

        content = copy.deepcopy(active.content_json or {})
        day_plans = content.get("day_plans")
        if not isinstance(day_plans, list):
            return _resp_error("Current itinerary content is invalid (missing day_plans)", status=500)

        removed = None
        applied_day = None
        added = []
        move_to_day = parsed.get("target_day")
        if move_to_day is not None:
            try:
                move_to_day = int(move_to_day)
            except (TypeError, ValueError):
                move_to_day = None

        source_plan = None
        if day is not None:
            source_plan = _find_day_plan(day_plans, day)
            if not source_plan:
                return _resp_error(f"Day {day} not found in current itinerary", status=400)
        else:
            strong_matches = []
            weak_matches = []
            for dp in day_plans:
                idx, score = _pick_poi_match(dp.get("pois") or [], poi_name)
                if idx >= 0:
                    if score >= 2:
                        strong_matches.append(dp)
                    else:
                        weak_matches.append(dp)
            if len(strong_matches) == 0 and len(weak_matches) == 0:
                return _resp_error(f"POI not found: {poi_name}", status=404)
            if len(strong_matches) > 1:
                days = [m.get("day") for m in strong_matches]
                return _resp_clarify(
                    trip_id,
                    parsed,
                    f"'{poi_name}' appears in multiple days {days}. Which day do you want to edit?",
                )
            if len(strong_matches) == 1:
                source_plan = strong_matches[0]
            elif len(weak_matches) == 1:
                source_plan = weak_matches[0]
            else:
                days = [m.get("day") for m in weak_matches]
                return _resp_clarify(
                    trip_id,
                    parsed,
                    (
                        f"I found multiple possible POIs matching '{poi_name}' in days {days}. "
                        "Please tell me the day number or full POI name."
                    ),
                )

        if intent == "move_poi":
            if move_to_day is None:
                return _resp_clarify(trip_id, parsed, "Which target day do you want to move it to?")
            target_plan = _find_day_plan(day_plans, move_to_day)
            if not target_plan:
                return _resp_error(f"Target day {move_to_day} not found", status=400)
            if int(target_plan.get("day", 0)) == int(source_plan.get("day", -1)):
                return _resp_error("Source day and target day are the same", status=400)

            moved = _pop_poi_from_plan(source_plan, poi_name)
            if not moved:
                return _resp_error(f"POI not found in source day: {poi_name}", status=404)
            ok, reason = _append_poi_to_plan_with_constraints(target_plan, moved, trip.preference)
            if not ok:
                source_pois = list(source_plan.get("pois") or [])
                source_pois.append(moved)
                source_plan["pois"] = source_pois
                _recompute_plan_meta(source_plan)
                return _resp_error(f"Failed to move POI: {reason}", status=400)

            removed = moved
            applied_day = source_plan.get("day")
            added = [moved]
        else:
            removed = _remove_poi_from_plan(source_plan, poi_name)
            applied_day = source_plan.get("day")

        if not removed:
            return _resp_error(f"POI not found in target day: {poi_name}", status=404)

        if intent in ("remove_poi", "replace_poi"):
            banned_uids = {_poi_uid(removed)} if isinstance(removed, dict) else set()
            pool = list(content.get("pois") or [])
            if not pool:
                pref = trip.preference
                generated = compute_candidates_and_itinerary(pref, limit=300) if pref else None
                pool = list((generated or {}).get("out") or [])
            if intent == "replace_poi":
                rep = _replace_one_poi_in_plan(
                    plan=source_plan,
                    day_plans=day_plans,
                    pool=pool,
                    pref=trip.preference,
                    removed_poi=removed,
                    constraints=parsed.get("constraints") or {},
                )
                if rep:
                    added = [rep]
            else:
                added = _refill_day_plan_from_pool(
                    plan=source_plan,
                    day_plans=day_plans,
                    pool=pool,
                    pref=trip.preference,
                    banned_uids=banned_uids,
                )

        trip_days = get_trip_days(trip.preference) if trip.preference else len(day_plans)
        content["day_plans"] = day_plans
        content["summary"] = format_itinerary_summary(day_plans, trip_days)

        new_row = _save_new_itinerary_version(trip_id, content)

        return _resp_applied(
            trip_id=trip_id,
            new_version=new_row.version,
            applied_action={
                "intent": intent,
                "day": applied_day,
                "removed_poi": removed.get("name") if isinstance(removed, dict) else poi_name,
                "target_day": move_to_day if intent == "move_poi" else None,
                "added_pois": [x.get("name") for x in added if isinstance(x, dict)],
            },
            day_plans=day_plans,
            summary=content.get("summary"),
            parsed=parsed,
        )
