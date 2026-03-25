import copy
import json
import os
import re

from flask import jsonify, request, session
from addMustVisit import (
    assess_add_confidence_for_query as _add_assess_add_confidence_for_query,
    apply_choice_to_parsed as _add_apply_choice_to_parsed,
    enforce_add_parse_rules as _add_enforce_parse_rules,
    execute_add_must_visit as _execute_add_must_visit,
    fill_clarification_options as _add_fill_clarification_options,
    init_add_must_visit,
    preparse_add_candidate_gate as _add_preparse_candidate_gate,
    resolve_choice_from_text as _add_resolve_choice_from_text,
)
from removePOI import (
    apply_choice_to_parsed as _remove_apply_choice_to_parsed,
    enforce_remove_parse_rules as _remove_enforce_parse_rules,
    execute_remove_poi as _execute_remove_poi,
    resolve_choice_from_text as _remove_resolve_choice_from_text,
)
from movePOI import (
    enforce_move_parse_rules as _move_enforce_parse_rules,
    execute_move_poi as _execute_move_poi,
)
from replacePOI import execute_replace_poi as _execute_replace_poi
from adjust_day_plans import execute_adjust_day_plans as _execute_adjust_day_plans


_CTX = {}
_DIALOG_STATE = {}


def init_change_poi(
    *,
    db,
    Trip,
    Itinerary,
    POI,
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
            "POI": POI,
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
    init_add_must_visit(db=db, POI=POI)


def _ctx(key):
    if key not in _CTX:
        raise RuntimeError(f"changePOI is not initialized: missing '{key}'")
    return _CTX[key]


def _dialog_key(user_id, trip_id):
    return f"{int(user_id)}:{int(trip_id)}"


def _get_dialog_state(user_id, trip_id):
    return _DIALOG_STATE.get(_dialog_key(user_id, trip_id))


def _set_dialog_state(user_id, trip_id, *, parsed, clarification_type=None, clarification_options=None):
    _DIALOG_STATE[_dialog_key(user_id, trip_id)] = {
        "parsed": copy.deepcopy(parsed or {}),
        "clarification_type": clarification_type,
        "clarification_options": list(clarification_options or []),
    }


def _clear_dialog_state(user_id, trip_id):
    _DIALOG_STATE.pop(_dialog_key(user_id, trip_id), None)


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


def _build_itinerary_parse_context(day_plans, max_days=7, max_pois_per_day=10):
    if not isinstance(day_plans, list) or not day_plans:
        return ""
    lines = []
    for dp in day_plans[: max(1, int(max_days))]:
        day_num = (dp or {}).get("day")
        pois = (dp or {}).get("pois") or []
        names = []
        for p in pois[: max(1, int(max_pois_per_day))]:
            n = str((p or {}).get("name") or "").strip()
            if n:
                names.append(n)
        if names:
            lines.append(f"Day {day_num}: " + " | ".join(names))
    return "\n".join(lines)


def _get_active_itinerary_for_trip(trip_id):
    Itinerary = _ctx("Itinerary")
    return (
        Itinerary.query
        .filter_by(trip_id=trip_id, is_active=True)
        .order_by(Itinerary.version.desc())
        .first()
    )


def _enforce_parse_clarification_rules(parsed, user_text):
    parsed = parsed if isinstance(parsed, dict) else {}
    intent = str(parsed.get("intent") or "").strip()
    edit_intents = {"remove_poi", "replace_poi", "move_poi", "add_poi", "adjust_day_plans"}

    # For non-edit chat (e.g. "thanks"), never force itinerary clarification prompts.
    if intent not in edit_intents:
        parsed["needs_clarification"] = False
        parsed["clarification_question"] = None
        parsed["clarification_type"] = None
        parsed["clarification_options"] = None
        return parsed

    try:
        conf = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < 0.6 and not parsed.get("needs_clarification"):
        parsed["needs_clarification"] = True
        parsed["clarification_question"] = (
            "I am not fully sure I understood. Please tell me the day number and exact POI name."
        )

    if intent in edit_intents and intent != "adjust_day_plans":
        poi_name = str(parsed.get("poi_name") or "").strip()
        if not poi_name:
            parsed["needs_clarification"] = True
            parsed["clarification_question"] = "Which exact POI do you want to edit?"

    parsed = _add_enforce_parse_rules(parsed)
    parsed = _remove_enforce_parse_rules(parsed, user_text)
    parsed = _move_enforce_parse_rules(parsed)
    return parsed


def _resolve_choice_from_user_text(user_text, options, clarification_type=None):
    txt = str(user_text or "").strip().lower()
    if not txt:
        return None
    opts = [str(o or "").strip() for o in (options or []) if str(o or "").strip()]
    opts_lower = {o.lower(): o for o in opts}
    if txt in opts_lower:
        return opts_lower[txt]

    add_choice = _add_resolve_choice_from_text(user_text, opts, clarification_type)
    if add_choice:
        return add_choice

    remove_choice = _remove_resolve_choice_from_text(user_text, opts, clarification_type)
    if remove_choice:
        return remove_choice

    return None


def _apply_choice_to_parsed(parsed, choice, clarification_type=None):
    out = copy.deepcopy(parsed or {})
    out["constraints"] = dict(out.get("constraints") or {})
    out["needs_clarification"] = False
    out["clarification_question"] = None
    out["clarification_type"] = None
    out["clarification_options"] = None

    if choice in ("remove_only", "replace_nearby_same_type"):
        out = _remove_apply_choice_to_parsed(out, choice)
    elif (
        choice in ("add_direct", "replace_existing")
        or str(choice).startswith("day_")
        or clarification_type in (
            "choose_replace_target_poi",
            "confirm_add_candidate_yes_no",
            "choose_add_candidate_from_list",
        )
    ):
        out = _add_apply_choice_to_parsed(out, choice, clarification_type)
    return out


def _llm_parse_itinerary_edit(user_text, itinerary_context=""):
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
- If itinerary context is provided, prefer selecting poi_name from that list.
"""
    try:
        client = OpenAI(api_key=api_key)
        user_payload = (user_text or "").strip()
        if itinerary_context:
            user_payload = (
                "User request:\n"
                + (user_text or "")
                + "\n\nCurrent itinerary context (day -> POIs):\n"
                + itinerary_context
            )
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": parser_system},
                {"role": "user", "content": user_payload},
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
        parsed = _enforce_parse_clarification_rules(parsed, user_text)
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


def _pick_existing_poi_for_replace(plan):
    pois = list((plan or {}).get("pois") or [])
    if not pois:
        return None
    # Prefer replacing non-must-visit POI first.
    for i, p in enumerate(pois):
        if not (p or {}).get("is_must_visit"):
            return i, p
    return len(pois) - 1, pois[-1]


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
    @app.route("/api/itinerary/add-confidence", methods=["POST"])
    def assess_itinerary_add_confidence():
        data = request.get_json() or {}
        poi_name = (data.get("poi_name") or "").strip()
        if not poi_name:
            return _resp_error("poi_name is required", status=400)
        day_plans = data.get("day_plans")
        if not isinstance(day_plans, list):
            day_plans = []
        confidence = _add_assess_add_confidence_for_query(poi_name, day_plans=day_plans)
        return jsonify({"success": True, "confidence": confidence}), 200

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

        pending = _get_dialog_state(user_id, trip_id)
        if pending:
            p = copy.deepcopy(pending.get("parsed") or {})
            ctype = pending.get("clarification_type")
            opts = list(pending.get("clarification_options") or [])
            picked = _resolve_choice_from_user_text(user_text, opts, ctype)
            if picked:
                parsed = _apply_choice_to_parsed(p, picked, clarification_type=ctype)
                parsed = _enforce_parse_clarification_rules(parsed, user_text)
                active_for_opts = _get_active_itinerary_for_trip(trip_id)
                day_plans_for_opts = (active_for_opts.content_json or {}).get("day_plans") if (active_for_opts and isinstance(active_for_opts.content_json, dict)) else []
                pool_for_opts = (active_for_opts.content_json or {}).get("pois") if (active_for_opts and isinstance(active_for_opts.content_json, dict)) else []
                parsed = _add_preparse_candidate_gate(parsed, pool_for_opts, day_plans_for_opts, _pick_poi_match)
                parsed = _add_fill_clarification_options(parsed, day_plans_for_opts, pool_for_opts)
                if parsed.get("needs_clarification"):
                    _set_dialog_state(
                        user_id,
                        trip_id,
                        parsed=parsed,
                        clarification_type=parsed.get("clarification_type"),
                        clarification_options=parsed.get("clarification_options") or [],
                    )
                else:
                    _clear_dialog_state(user_id, trip_id)
                return _resp_parsed(trip_id, parsed)
            if ctype in ("choose_replace_target_poi", "reenter_add_poi_name"):
                parsed = copy.deepcopy(p)
                parsed["constraints"] = dict(parsed.get("constraints") or {})
                if ctype == "choose_replace_target_poi":
                    parsed["constraints"]["replace_poi_name"] = user_text
                else:
                    parsed["poi_name"] = user_text
                    parsed["constraints"].pop("candidate_selected", None)
                    parsed["constraints"].pop("proposed_poi_name", None)
                parsed = _enforce_parse_clarification_rules(parsed, user_text)
                active_for_opts = _get_active_itinerary_for_trip(trip_id)
                day_plans_for_opts = (active_for_opts.content_json or {}).get("day_plans") if (active_for_opts and isinstance(active_for_opts.content_json, dict)) else []
                pool_for_opts = (active_for_opts.content_json or {}).get("pois") if (active_for_opts and isinstance(active_for_opts.content_json, dict)) else []
                parsed = _add_preparse_candidate_gate(parsed, pool_for_opts, day_plans_for_opts, _pick_poi_match)
                parsed = _add_fill_clarification_options(parsed, day_plans_for_opts, pool_for_opts)
                if parsed.get("needs_clarification"):
                    _set_dialog_state(
                        user_id,
                        trip_id,
                        parsed=parsed,
                        clarification_type=parsed.get("clarification_type"),
                        clarification_options=parsed.get("clarification_options") or [],
                    )
                else:
                    _clear_dialog_state(user_id, trip_id)
                return _resp_parsed(trip_id, parsed)
            # User input does not match current pending clarification options.
            # Treat it as a fresh request so resumed-history edits behave the
            # same as new-session edits (avoid stale pending state hijacking).
            _clear_dialog_state(user_id, trip_id)

        active = _get_active_itinerary_for_trip(trip_id)
        context = _build_itinerary_parse_context((active.content_json or {}).get("day_plans") if active and isinstance(active.content_json, dict) else [])
        parsed, err = _llm_parse_itinerary_edit(user_text, itinerary_context=context)
        if err:
            return _resp_error(err, status=500)

        day_plans_for_opts = (active.content_json or {}).get("day_plans") if (active and isinstance(active.content_json, dict)) else []
        pool_for_opts = (active.content_json or {}).get("pois") if (active and isinstance(active.content_json, dict)) else []
        parsed = _add_preparse_candidate_gate(parsed, pool_for_opts, day_plans_for_opts, _pick_poi_match)
        parsed = _add_fill_clarification_options(parsed, day_plans_for_opts, pool_for_opts)
        if parsed.get("needs_clarification"):
            _set_dialog_state(
                user_id,
                trip_id,
                parsed=parsed,
                clarification_type=parsed.get("clarification_type"),
                clarification_options=parsed.get("clarification_options") or [],
            )
        else:
            _clear_dialog_state(user_id, trip_id)

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
        optimize_route_greedy_tsp = _ctx("optimize_route_greedy_tsp")
        get_poi_duration = _ctx("get_poi_duration")

        trip = db.session.get(Trip, trip_id)
        if not trip:
            return _resp_error("Trip not found.", status=404)
        if trip.user_id != user_id:
            return _resp_error("You do not have access to this trip.", status=403)

        active = _get_active_itinerary_for_trip(trip_id)
        parse_context = _build_itinerary_parse_context((active.content_json or {}).get("day_plans") if active and isinstance(active.content_json, dict) else [])

        data = request.get_json() or {}
        parsed = data.get("parsed") if isinstance(data.get("parsed"), dict) else None
        if not parsed:
            user_text = (data.get("user_text") or "").strip()
            if not user_text:
                return _resp_error("parsed or user_text is required", status=400)
            parsed, err = _llm_parse_itinerary_edit(user_text, itinerary_context=parse_context)
            if err:
                return _resp_error(err, status=500)

        if parsed.get("needs_clarification"):
            day_plans_for_opts = (active.content_json or {}).get("day_plans") if (active and isinstance(active.content_json, dict)) else []
            pool_for_opts = (active.content_json or {}).get("pois") if (active and isinstance(active.content_json, dict)) else []
            parsed = _add_fill_clarification_options(parsed, day_plans_for_opts, pool_for_opts)
            return _resp_clarify(
                trip_id,
                parsed,
                parsed.get("clarification_question"),
                clarification_type=parsed.get("clarification_type"),
                clarification_options=parsed.get("clarification_options"),
            )

        intent = (parsed.get("intent") or "").strip()
        if intent not in ("remove_poi", "replace_poi", "move_poi", "add_poi", "adjust_day_plans"):
            return _resp_error(
                f"Unsupported intent for MVP apply-edit: {intent or 'unknown'} (currently supports remove_poi/replace_poi/move_poi/add_poi/adjust_day_plans)",
                status=400,
                parsed=parsed,
            )

        day = parsed.get("day")
        poi_name = (parsed.get("poi_name") or "").strip()
        if intent != "adjust_day_plans" and not poi_name:
            return _resp_error(
                "poi_name is required for remove_poi/replace_poi/move_poi/add_poi",
                status=400,
                parsed=parsed,
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
        if intent == "replace_poi":
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

        if intent == "adjust_day_plans":
            adjust_result = _execute_adjust_day_plans(
                parsed=parsed,
                content=content,
                trip=trip,
                compute_candidates_and_itinerary=compute_candidates_and_itinerary,
                daily_poi_cap_from_pref=_daily_poi_cap_from_pref,
                recompute_plan_meta=_recompute_plan_meta,
            )
            if adjust_result.get("kind") == "error":
                return _resp_error(
                    adjust_result.get("message") or "Failed to adjust trip days",
                    status=int(adjust_result.get("status") or 400),
                    parsed=parsed,
                )
            day_plans = list(adjust_result.get("day_plans") or [])
            content["day_plans"] = day_plans
            applied_day = int(adjust_result.get("applied_day") or 1)
            removed = None
            added = []
            move_to_day = adjust_result.get("target_day")
        elif intent == "remove_poi":
            remove_result = _execute_remove_poi(
                parsed=parsed,
                poi_name=poi_name,
                day=day,
                day_plans=day_plans,
                content=content,
                trip=trip,
                compute_candidates_and_itinerary=compute_candidates_and_itinerary,
                find_day_plan=_find_day_plan,
                pick_poi_match=_pick_poi_match,
                optimize_route_greedy_tsp=optimize_route_greedy_tsp,
                get_poi_duration=get_poi_duration,
                refill_day_plan_from_pool=_refill_day_plan_from_pool,
                poi_uid=_poi_uid,
            )
            if remove_result.get("kind") == "error":
                return _resp_error(
                    remove_result.get("message") or "Failed to remove POI",
                    status=int(remove_result.get("status") or 400),
                    parsed=parsed,
                )
            if remove_result.get("kind") == "clarify":
                parsed = remove_result.get("parsed") or parsed
                return _resp_clarify(
                    trip_id,
                    parsed,
                    remove_result.get("question"),
                    clarification_type=remove_result.get("clarification_type"),
                    clarification_options=remove_result.get("clarification_options"),
                )
            removed = remove_result.get("removed")
            applied_day = remove_result.get("applied_day")
            added = list(remove_result.get("added") or [])
        elif intent == "add_poi":
            add_result = _execute_add_must_visit(
                parsed=parsed,
                poi_name=poi_name,
                day=day,
                day_plans=day_plans,
                content=content,
                trip=trip,
                compute_candidates_and_itinerary=compute_candidates_and_itinerary,
                find_day_plan=_find_day_plan,
                pick_poi_match=_pick_poi_match,
                poi_uid=_poi_uid,
                recompute_plan_meta=_recompute_plan_meta,
                append_with_constraints=_append_poi_to_plan_with_constraints,
            )
            if add_result.get("kind") == "error":
                return _resp_error(
                    add_result.get("message") or "Failed to add POI",
                    status=int(add_result.get("status") or 400),
                    parsed=parsed,
                )
            if add_result.get("kind") == "clarify":
                parsed = add_result.get("parsed") or parsed
                return _resp_clarify(
                    trip_id,
                    parsed,
                    add_result.get("question"),
                    clarification_type=add_result.get("clarification_type"),
                    clarification_options=add_result.get("clarification_options"),
                )
            removed = add_result.get("removed")
            applied_day = add_result.get("applied_day")
            added = list(add_result.get("added") or [])
        elif intent == "move_poi":
            move_result = _execute_move_poi(
                parsed=parsed,
                poi_name=poi_name,
                day=day,
                target_day=move_to_day,
                day_plans=day_plans,
                find_day_plan=_find_day_plan,
                pick_poi_match=_pick_poi_match,
                recompute_plan_meta=_recompute_plan_meta,
            )
            if move_result.get("kind") == "error":
                return _resp_error(
                    move_result.get("message") or "Failed to move POI",
                    status=int(move_result.get("status") or 400),
                    parsed=parsed,
                )
            if move_result.get("kind") == "clarify":
                parsed = move_result.get("parsed") or parsed
                return _resp_clarify(
                    trip_id,
                    parsed,
                    move_result.get("question"),
                    clarification_type=move_result.get("clarification_type"),
                    clarification_options=move_result.get("clarification_options"),
                )
            removed = move_result.get("removed")
            applied_day = move_result.get("applied_day")
            added = list(move_result.get("added") or [])
            move_to_day = move_result.get("target_day")
        elif intent == "replace_poi":
            pool = list(content.get("pois") or [])
            if not pool:
                pref = trip.preference
                generated = compute_candidates_and_itinerary(pref, limit=300) if pref else None
                pool = list((generated or {}).get("out") or [])
            replace_result = _execute_replace_poi(
                parsed=parsed,
                day_plans=day_plans,
                find_day_plan=_find_day_plan,
                pick_poi_match=_pick_poi_match,
                recompute_plan_meta=_recompute_plan_meta,
                replace_one_poi_in_plan=_replace_one_poi_in_plan,
                pool=pool,
                pref=trip.preference,
                removed_poi_placeholder_name=poi_name,
            )
            if replace_result.get("kind") == "error":
                return _resp_error(
                    replace_result.get("message") or "Failed to replace POI",
                    status=int(replace_result.get("status") or 400),
                    parsed=parsed,
                )
            if replace_result.get("kind") == "clarify":
                parsed = replace_result.get("parsed") or parsed
                return _resp_clarify(
                    trip_id,
                    parsed,
                    replace_result.get("question"),
                    clarification_type=replace_result.get("clarification_type"),
                    clarification_options=replace_result.get("clarification_options"),
                )
            if replace_result.get("kind") == "pass_through":
                rep = _replace_one_poi_in_plan(
                    plan=source_plan,
                    day_plans=day_plans,
                    pool=pool,
                    pref=trip.preference,
                    removed_poi={"name": poi_name},
                    constraints=parsed.get("constraints") or {},
                )
                removed = {"name": poi_name}
                applied_day = source_plan.get("day") if source_plan else day
                added = [rep] if rep else []
            else:
                removed = replace_result.get("removed")
                applied_day = replace_result.get("applied_day")
                added = list(replace_result.get("added") or [])
                move_to_day = replace_result.get("target_day") or move_to_day
        if intent not in ("add_poi", "adjust_day_plans") and not removed:
            return _resp_error(f"POI not found in target day: {poi_name}", status=404)

        trip_days = get_trip_days(trip.preference) if trip.preference else len(day_plans)
        content["day_plans"] = day_plans
        content["summary"] = format_itinerary_summary(day_plans, trip_days)

        new_row = _save_new_itinerary_version(trip_id, content)
        _clear_dialog_state(user_id, trip_id)

        return _resp_applied(
            trip_id=trip_id,
            new_version=new_row.version,
            applied_action={
                "intent": intent,
                "day": applied_day,
                "removed_poi": (
                    (removed.get("name") if isinstance(removed, dict) else poi_name)
                    if intent in ("remove_poi", "replace_poi", "move_poi")
                    else None
                ),
                "target_day": move_to_day if intent in ("move_poi", "replace_poi", "adjust_day_plans") else None,
                "added_pois": [x.get("name") for x in added if isinstance(x, dict)],
            },
            day_plans=day_plans,
            summary=content.get("summary"),
            parsed=parsed,
        )
