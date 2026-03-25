import copy
import os
import re


_CTX = {}
_GMAPS_CLIENT = None


def init_add_must_visit(*, db, POI):
    _CTX.clear()
    _CTX.update({"db": db, "POI": POI})


def _ctx(key):
    if key not in _CTX:
        raise RuntimeError(f"addMustVisit is not initialized: missing '{key}'")
    return _CTX[key]


def enforce_add_parse_rules(parsed):
    parsed = parsed if isinstance(parsed, dict) else {}
    intent = str(parsed.get("intent") or "").strip().lower()
    if intent != "add_poi":
        return parsed

    constraints = parsed.get("constraints") if isinstance(parsed.get("constraints"), dict) else {}
    add_mode = str(constraints.get("add_mode") or "").strip().lower()
    if add_mode not in ("append", "replace"):
        parsed["needs_clarification"] = True
        parsed["clarification_type"] = "add_mode_choice"
        parsed["clarification_options"] = ["add_direct", "replace_existing"]
        parsed["clarification_question"] = (
            "I can add it to your itinerary. Do you want to replace an existing POI or add directly to a day?"
        )
    elif add_mode == "append" and parsed.get("day") is None:
        parsed["needs_clarification"] = True
        parsed["clarification_type"] = "choose_day_for_add"
        parsed.setdefault("clarification_options", [])
        parsed["clarification_question"] = "On which day would you like to add it?"
    elif add_mode == "replace":
        replace_poi_name = str(constraints.get("replace_poi_name") or "").strip()
        if not replace_poi_name:
            parsed["needs_clarification"] = True
            parsed["clarification_type"] = "choose_replace_target_poi"
            parsed.setdefault("clarification_options", [])
            parsed["clarification_question"] = "Which POI do you want to replace?"
    return parsed


def resolve_choice_from_text(user_text, options, clarification_type=None):
    txt = str(user_text or "").strip().lower()
    if not txt:
        return None
    opts = [str(o or "").strip() for o in (options or []) if str(o or "").strip()]
    opts_lower = {o.lower(): o for o in opts}
    if txt in opts_lower:
        return opts_lower[txt]

    if clarification_type == "add_mode_choice":
        if "replace" in txt and "replace_existing" in opts:
            return "replace_existing"
        if any(k in txt for k in ("add", "append", "direct")) and "add_direct" in opts:
            return "add_direct"

    if clarification_type == "choose_day_for_add":
        m = re.search(r"\bday\s*(\d+)\b", txt)
        if not m:
            m = re.search(r"^\s*(\d+)\s*$", txt)
        if m:
            day_opt = f"day_{int(m.group(1))}"
            if day_opt in opts:
                return day_opt

    if clarification_type == "choose_replace_target_poi":
        for o in opts:
            if txt == str(o).strip().lower():
                return o

    return None


def apply_choice_to_parsed(parsed, choice, clarification_type=None):
    out = copy.deepcopy(parsed or {})
    out["constraints"] = dict(out.get("constraints") or {})

    if choice == "add_direct":
        out["intent"] = "add_poi"
        out["constraints"]["add_mode"] = "append"
    elif choice == "replace_existing":
        out["intent"] = "add_poi"
        out["constraints"]["add_mode"] = "replace"
    elif str(choice).startswith("day_"):
        try:
            out["day"] = int(str(choice).split("_", 1)[1])
        except Exception:
            pass
    elif clarification_type == "choose_replace_target_poi":
        out["intent"] = "add_poi"
        out["constraints"]["add_mode"] = "replace"
        out["constraints"]["replace_poi_name"] = str(choice or "").strip()
    return out


def day_choice_options(day_plans):
    options = []
    for dp in day_plans or []:
        d = (dp or {}).get("day")
        try:
            d_int = int(d)
        except (TypeError, ValueError):
            continue
        options.append(f"day_{d_int}")
    return options


def replace_target_poi_options(day_plans, limit=16):
    names = []
    seen = set()
    for dp in day_plans or []:
        for p in (dp.get("pois") or []):
            n = str((p or {}).get("name") or "").strip()
            if not n:
                continue
            k = n.lower()
            if k in seen:
                continue
            seen.add(k)
            names.append(n)
            if len(names) >= int(limit):
                return names
    return names


def fill_clarification_options(parsed, day_plans):
    ctype = parsed.get("clarification_type")
    if ctype == "choose_day_for_add" and not parsed.get("clarification_options"):
        parsed["clarification_options"] = day_choice_options(day_plans)
    if ctype == "choose_replace_target_poi" and not parsed.get("clarification_options"):
        parsed["clarification_options"] = replace_target_poi_options(day_plans)
    return parsed


def _norm_poi_name(name):
    s = str(name or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _get_gmaps_client():
    global _GMAPS_CLIENT
    if _GMAPS_CLIENT is not None:
        return _GMAPS_CLIENT
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    try:
        import googlemaps

        _GMAPS_CLIENT = googlemaps.Client(key=key)
        return _GMAPS_CLIENT
    except Exception:
        return None


def _poi_row_to_candidate(poi_row):
    if not poi_row:
        return None
    filter_ids = []
    try:
        filter_ids = [f.filter_id for f in (poi_row.filters or []) if getattr(f, "filter_id", None) is not None]
    except Exception:
        filter_ids = []
    return {
        "poi_id": getattr(poi_row, "poi_id", None),
        "place_id": getattr(poi_row, "google_place_id", None),
        "name": getattr(poi_row, "name", None),
        "latitude": getattr(poi_row, "latitude", None),
        "longitude": getattr(poi_row, "longitude", None),
        "filter_ids": filter_ids,
        "duration": 2.0,
    }


def _resolve_target_poi_with_google_or_db(poi_name):
    db = _ctx("db")
    POI = _ctx("POI")
    token = str(poi_name or "").strip()
    if not token:
        return None

    try:
        local = POI.query.filter(db.func.lower(POI.name).contains(token.lower())).first()
        cand = _poi_row_to_candidate(local)
        if cand:
            return cand
    except Exception:
        pass

    gmaps_client = _get_gmaps_client()
    if not gmaps_client:
        return None
    try:
        place = gmaps_client.find_place(
            f"{token} Dublin",
            "textquery",
            fields=["name", "place_id", "geometry"],
        )
        candidates = place.get("candidates") or []
        if not candidates:
            return None
        c0 = candidates[0]
        g_place_id = c0.get("place_id")
        g_name = c0.get("name")

        if g_place_id:
            row = POI.query.filter_by(google_place_id=g_place_id).first()
            cand = _poi_row_to_candidate(row)
            if cand:
                return cand

        if g_name:
            row = POI.query.filter(db.func.lower(POI.name).contains(g_name.lower())).first()
            cand = _poi_row_to_candidate(row)
            if cand:
                return cand

        lat, lng = None, None
        geom = c0.get("geometry")
        if isinstance(geom, dict) and isinstance(geom.get("location"), dict):
            lat = geom["location"].get("lat")
            lng = geom["location"].get("lng")
        return {
            "poi_id": None,
            "place_id": g_place_id,
            "name": g_name or token,
            "latitude": float(lat) if lat is not None else None,
            "longitude": float(lng) if lng is not None else None,
            "filter_ids": [],
            "duration": 2.0,
        }
    except Exception:
        return None


def resolve_target_poi_from_pool(pool, poi_name, pick_poi_match):
    candidates = list(pool or [])
    idx, score = pick_poi_match(candidates, poi_name)
    if idx >= 0 and score >= 2:
        try:
            return candidates[idx]
        except Exception:
            pass

    token = _norm_poi_name(poi_name)
    if token:
        token_compact = token.replace(" ", "")
        for c in candidates:
            name = str((c or {}).get("name") or "").strip()
            if not name:
                continue
            words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w]
            initials = "".join(w[0] for w in words).lower()
            if token_compact and initials == token_compact:
                return c

    return _resolve_target_poi_with_google_or_db(poi_name)


def execute_add_must_visit(
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
    poi_uid,
    recompute_plan_meta,
    append_with_constraints,
):
    pool = list(content.get("pois") or [])
    if not pool:
        pref = trip.preference
        generated = compute_candidates_and_itinerary(pref, limit=300) if pref else None
        pool = list((generated or {}).get("out") or [])

    target_poi = resolve_target_poi_from_pool(pool, poi_name, pick_poi_match)
    if not target_poi:
        return {
            "kind": "clarify",
            "question": f"I couldn't confidently find '{poi_name}' in candidate POIs. Please provide a fuller POI name.",
            "parsed": parsed,
        }

    target_uid = poi_uid(target_poi)
    for dp in day_plans:
        for p in (dp.get("pois") or []):
            if target_uid and poi_uid(p) == target_uid:
                return {"kind": "error", "status": 400, "message": f"'{target_poi.get('name') or poi_name}' is already in your itinerary."}

    constraints = parsed.get("constraints") if isinstance(parsed.get("constraints"), dict) else {}
    add_mode = str(constraints.get("add_mode") or "").strip().lower()
    if add_mode not in ("append", "replace"):
        parsed["needs_clarification"] = True
        parsed["clarification_type"] = "add_mode_choice"
        parsed["clarification_options"] = ["add_direct", "replace_existing"]
        parsed["clarification_question"] = (
            "I can add it to your itinerary. Do you want to replace an existing POI or add directly to a day?"
        )
        return {
            "kind": "clarify",
            "question": parsed["clarification_question"],
            "clarification_type": parsed["clarification_type"],
            "clarification_options": parsed["clarification_options"],
            "parsed": parsed,
        }

    chosen_day = day
    if add_mode == "append":
        if chosen_day is None:
            parsed["needs_clarification"] = True
            parsed["clarification_type"] = "choose_day_for_add"
            parsed["clarification_options"] = day_choice_options(day_plans)
            parsed["clarification_question"] = "Which day do you want to add it to?"
            return {
                "kind": "clarify",
                "question": parsed["clarification_question"],
                "clarification_type": parsed["clarification_type"],
                "clarification_options": parsed["clarification_options"],
                "parsed": parsed,
            }
        target_plan = find_day_plan(day_plans, chosen_day)
        if not target_plan:
            return {"kind": "error", "status": 400, "message": f"Day {chosen_day} not found in current itinerary"}

        # User explicitly requested to always append for add-to-day flow, even if day cap is reached.
        pois = list(target_plan.get("pois") or [])
        pois.append(target_poi)
        target_plan["pois"] = pois
        recompute_plan_meta(target_plan)
        return {"kind": "applied", "applied_day": target_plan.get("day"), "added": [target_poi], "removed": None}

    replace_poi_name = str((constraints or {}).get("replace_poi_name") or "").strip()
    if not replace_poi_name:
        parsed["needs_clarification"] = True
        parsed["clarification_type"] = "choose_replace_target_poi"
        parsed["clarification_options"] = replace_target_poi_options(day_plans)
        parsed["clarification_question"] = "Which POI do you want to replace?"
        return {
            "kind": "clarify",
            "question": parsed["clarification_question"],
            "clarification_type": parsed["clarification_type"],
            "clarification_options": parsed["clarification_options"],
            "parsed": parsed,
        }

    if chosen_day is not None:
        target_plan = find_day_plan(day_plans, chosen_day)
        if not target_plan:
            return {"kind": "error", "status": 400, "message": f"Day {chosen_day} not found in current itinerary"}
        ridx, _ = pick_poi_match(target_plan.get("pois") or [], replace_poi_name)
        if ridx < 0:
            return {
                "kind": "error",
                "status": 404,
                "message": f"POI not found in Day {chosen_day}: {replace_poi_name}",
            }
    else:
        strong_matches = []
        weak_matches = []
        for dp in day_plans:
            idx2, score2 = pick_poi_match(dp.get("pois") or [], replace_poi_name)
            if idx2 >= 0:
                if score2 >= 2:
                    strong_matches.append((dp, idx2, score2))
                else:
                    weak_matches.append((dp, idx2, score2))
        if len(strong_matches) > 1:
            days = [m[0].get("day") for m in strong_matches]
            parsed["needs_clarification"] = True
            parsed["clarification_type"] = "choose_day_for_add"
            parsed["clarification_options"] = [f"day_{int(d)}" for d in days if d is not None]
            parsed["clarification_question"] = f"'{replace_poi_name}' appears in multiple days {days}. Which day should I replace it in?"
            return {
                "kind": "clarify",
                "question": parsed["clarification_question"],
                "clarification_type": parsed["clarification_type"],
                "clarification_options": parsed["clarification_options"],
                "parsed": parsed,
            }
        picked_plan = strong_matches[0] if len(strong_matches) == 1 else (weak_matches[0] if len(weak_matches) == 1 else None)
        if not picked_plan:
            return {"kind": "error", "status": 404, "message": f"POI to replace not found: {replace_poi_name}"}
        target_plan, ridx, _ = picked_plan

    pois = list(target_plan.get("pois") or [])
    try:
        removed_obj = pois.pop(ridx)
    except Exception:
        return {"kind": "error", "status": 400, "message": "Failed to replace: target index invalid"}
    target_plan["pois"] = pois
    recompute_plan_meta(target_plan)

    # User-requested behavior: replace flow should also ignore daily cap constraints.
    replaced_pois = list(target_plan.get("pois") or [])
    replaced_pois.append(target_poi)
    target_plan["pois"] = replaced_pois
    recompute_plan_meta(target_plan)

    return {
        "kind": "applied",
        "applied_day": target_plan.get("day"),
        "added": [target_poi],
        "removed": removed_obj,
    }
