import copy
import os
import re
from difflib import SequenceMatcher


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
    ctype = str(parsed.get("clarification_type") or "").strip()
    if parsed.get("needs_clarification") and ctype in (
        "confirm_add_candidate_yes_no",
        "choose_add_candidate_from_list",
        "reenter_add_poi_name",
    ):
        return parsed
    poi_name = str(parsed.get("poi_name") or "").strip()
    if poi_name and _is_broad_add_query(poi_name) and not bool(constraints.get("candidate_selected")):
        parsed["needs_clarification"] = True
        parsed["clarification_type"] = "choose_add_candidate_from_list"
        parsed.setdefault("clarification_options", [])
        parsed["clarification_question"] = (
            f"'{poi_name}' is too broad or unclear. Please choose one from the top matches or type again."
        )
        return parsed

    add_mode = str(constraints.get("add_mode") or "").strip().lower()
    if add_mode not in ("append", "replace"):
        # Defer add_mode prompt to execution stage so we can run confidence gating first.
        if not str(parsed.get("poi_name") or "").strip():
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
            day_num = int(m.group(1))
            day_opt = f"day_{day_num}"
            # Accept common option formats: day_2 / 2 / day 2.
            if not opts:
                return day_opt
            normalized_opts = {str(o).strip().lower() for o in opts}
            if (
                day_opt in normalized_opts
                or str(day_num) in normalized_opts
                or f"day {day_num}" in normalized_opts
            ):
                return day_opt

    if clarification_type == "choose_replace_target_poi":
        for o in opts:
            if txt == str(o).strip().lower():
                return o

    if clarification_type == "confirm_add_candidate_yes_no":
        if txt in ("yes", "y", "confirm") and "confirm_yes" in opts:
            return "confirm_yes"
        if txt in ("no", "n", "retype", "retry") and "confirm_no" in opts:
            return "confirm_no"

    if clarification_type == "choose_add_candidate_from_list":
        if txt in ("type again", "retype", "retry", "other") and "type_again" in opts:
            return "type_again"
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
    elif clarification_type == "confirm_add_candidate_yes_no":
        if choice == "confirm_yes":
            proposed = str(out["constraints"].get("proposed_poi_name") or "").strip()
            if proposed:
                out["poi_name"] = proposed
                out["constraints"]["candidate_selected"] = True
                out["constraints"]["candidate_selected_from_clarification"] = True
        elif choice == "confirm_no":
            rejected = str(out["constraints"].get("proposed_poi_name") or "").strip()
            out["needs_clarification"] = True
            out["clarification_type"] = "choose_add_candidate_from_list"
            out["clarification_question"] = (
                "Is the place you want to visit in these options? "
                "If not, please type the exact POI name you want to add."
            )
            out["clarification_options"] = []
            if rejected:
                out["constraints"]["rejected_poi_name"] = rejected
        out["constraints"].pop("proposed_poi_name", None)
    elif clarification_type == "choose_add_candidate_from_list":
        if choice == "type_again":
            out["needs_clarification"] = True
            out["clarification_type"] = "reenter_add_poi_name"
            out["clarification_question"] = "Please type the exact POI name you want to add."
            out["clarification_options"] = []
        else:
            out["poi_name"] = str(choice or "").strip()
            out["constraints"]["candidate_selected"] = True
            out["constraints"]["candidate_selected_from_clarification"] = True
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


def fill_clarification_options(parsed, day_plans, pool=None):
    ctype = parsed.get("clarification_type")
    if ctype == "choose_day_for_add" and not parsed.get("clarification_options"):
        parsed["clarification_options"] = day_choice_options(day_plans)
    if ctype == "choose_replace_target_poi" and not parsed.get("clarification_options"):
        parsed["clarification_options"] = replace_target_poi_options(day_plans)
    if ctype == "choose_add_candidate_from_list" and not parsed.get("clarification_options"):
        poi_name = str(parsed.get("poi_name") or "").strip()
        constraints = parsed.get("constraints") if isinstance(parsed.get("constraints"), dict) else {}
        rejected_name = str(constraints.get("rejected_poi_name") or "").strip()
        rejected_norm = _norm_poi_name(rejected_name) if rejected_name else ""
        existing_keys = _existing_poi_name_keys(day_plans)
        if rejected_norm:
            existing_keys.add(rejected_norm)

        # Per product requirement, low/medium suggestion lists must come from Google search ranking.
        options = _google_top_candidate_names_for_query(
            poi_name,
            limit=5,
            exclude_keys=existing_keys,
        )
        if "type_again" not in options:
            options.append("type_again")
        parsed["clarification_options"] = options
    return parsed


def _norm_poi_name(name):
    s = str(name or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_GENERIC_ADD_TERMS = {
    "museum", "museums", "park", "parks", "church", "cathedral",
    "gallery", "bar", "pub", "restaurant", "food", "shopping",
    "attraction", "attractions", "place", "places", "landmark",
}


def _is_broad_add_query(poi_name):
    token = _norm_poi_name(poi_name)
    words = [w for w in token.split(" ") if w]
    return len(words) <= 1 and token in _GENERIC_ADD_TERMS


def _existing_poi_name_keys(day_plans):
    keys = set()
    for dp in day_plans or []:
        for p in (dp.get("pois") or []):
            k = _norm_poi_name((p or {}).get("name"))
            if k:
                keys.add(k)
    return keys


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


def _google_top_matches(poi_name, limit=5):
    token = str(poi_name or "").strip()
    if not token:
        return []
    gmaps_client = _get_gmaps_client()
    if not gmaps_client:
        return []
    try:
        # Prefer Places Text Search for ranking-style top-N results.
        place = gmaps_client.places(query=f"{token} Dublin")
        candidates = place.get("results") or []
        if not candidates:
            # Fallback for environments where places() is restricted/unavailable.
            place = gmaps_client.find_place(
                f"{token} Dublin",
                "textquery",
                fields=["name", "place_id", "geometry"],
            )
            candidates = place.get("candidates") or []
        out = []
        seen = set()
        for c in candidates:
            name = str(c.get("name") or "").strip()
            if not name:
                continue
            k = _norm_poi_name(name)
            if not k or k in seen:
                continue
            seen.add(k)
            lat, lng = None, None
            geom = c.get("geometry")
            if isinstance(geom, dict) and isinstance(geom.get("location"), dict):
                lat = geom["location"].get("lat")
                lng = geom["location"].get("lng")
            out.append(
                {
                    "poi_id": None,
                    "place_id": c.get("place_id"),
                    "name": name,
                    "latitude": float(lat) if lat is not None else None,
                    "longitude": float(lng) if lng is not None else None,
                    "filter_ids": [],
                    "duration": 2.0,
                }
            )
            if len(out) >= int(limit):
                break
        return out
    except Exception:
        return []


def _google_top_candidate_names_for_query(poi_name, limit=8, exclude_keys=None):
    exclude = set(exclude_keys or set())
    out = []
    seen = set()
    for c in _google_top_matches(poi_name, limit=max(12, int(limit) * 2)):
        name = str((c or {}).get("name") or "").strip()
        if not name:
            continue
        key = _norm_poi_name(name)
        if not key or key in seen or key in exclude:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= int(limit):
            break
    return out


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


def _top_candidate_names_for_query(pool, poi_name, limit=8, exclude_keys=None):
    items = list(pool or [])
    token = _norm_poi_name(poi_name)
    token_words = [w for w in token.split(" ") if w]
    exclude = set(exclude_keys or set())
    scored = []
    for c in items:
        name = str((c or {}).get("name") or "").strip()
        if not name:
            continue
        n = _norm_poi_name(name)
        if n in exclude:
            continue
        score = 0
        if token and n == token:
            score = 100
        elif token and (token in n or n in token):
            score = 60
        elif token_words and any(w and w in n for w in token_words):
            score = 30
        elif token:
            score = 1
        scored.append((score, name))
    scored.sort(key=lambda x: (-x[0], x[1].lower()))
    out = []
    seen = set()
    for score, name in scored:
        k = name.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(name)
        if len(out) >= int(limit):
            break
    return out


def _candidate_rankings_for_query(pool, poi_name, exclude_keys=None):
    items = list(pool or [])
    token = _norm_poi_name(poi_name)
    q_words = [w for w in token.split(" ") if w]
    q_set = set(q_words)
    exclude = set(exclude_keys or set())
    ranked = []
    for c in items:
        name = str((c or {}).get("name") or "").strip()
        if not name:
            continue
        n = _norm_poi_name(name)
        if not n or n in exclude:
            continue
        c_words = [w for w in n.split(" ") if w]
        c_set = set(c_words)
        sim = SequenceMatcher(None, token, n).ratio() if token else 0.0
        overlap = (len(q_set & c_set) / max(1, len(q_set))) if q_set else 0.0
        contains_bonus = 0.12 if (token and (token in n or n in token)) else 0.0
        prefix_bonus = 0.08 if (token and n.startswith(token[: max(1, min(5, len(token))) ])) else 0.0
        score = max(sim, overlap * 0.92) + contains_bonus + prefix_bonus
        ranked.append((score, c))
    ranked.sort(key=lambda x: (-x[0], str((x[1] or {}).get("name") or "").lower()))
    return ranked


def _assess_add_target_confidence(pool, poi_name, pick_poi_match, day_plans=None):
    candidates = list(pool or [])
    exclude = _existing_poi_name_keys(day_plans)
    token = _norm_poi_name(poi_name)
    token_words = [w for w in token.split(" ") if w]
    is_generic = len(token_words) <= 1 and token in _GENERIC_ADD_TERMS

    # Product rule: one-word add input should always be treated as low confidence.
    if len(token_words) == 1 and token:
        top = _google_top_candidate_names_for_query(poi_name, limit=8, exclude_keys=exclude)
        return {"level": "low", "candidates": top}

    if is_generic:
        top = _google_top_candidate_names_for_query(poi_name, limit=8, exclude_keys=exclude)
        return {"level": "low", "candidates": top}

    google_matches = _google_top_matches(poi_name, limit=5)
    if google_matches:
        g_top = google_matches[0]
        g_name_norm = _norm_poi_name((g_top or {}).get("name"))
        if token and g_name_norm and g_name_norm == token:
            return {"level": "high", "candidate": g_top}
        if len(token_words) >= 2:
            return {"level": "medium", "candidate": g_top}

    ranked = _candidate_rankings_for_query(candidates, poi_name, exclude_keys=exclude)
    top1 = ranked[0] if ranked else None
    top2 = ranked[1] if len(ranked) > 1 else None
    top1_score = float(top1[0]) if top1 else 0.0
    top2_score = float(top2[0]) if top2 else 0.0
    margin = top1_score - top2_score
    best = top1[1] if top1 else None
    best_norm = _norm_poi_name((best or {}).get("name")) if best else ""

    # L1: exact / near-exact -> direct execute
    if best and token and (best_norm == token or (top1_score >= 1.0 and margin >= 0.12)):
        return {"level": "high", "candidate": best}

    # L2: likely typo/missing words with clear winner -> ask Yes/No
    if best and token and top1_score >= 0.62 and margin >= 0.12:
        return {"level": "medium", "candidate": best}

    # Fallback to Google/DB only for specific multi-word input
    if len(token_words) >= 2:
        ext = _resolve_target_poi_with_google_or_db(poi_name)
        if ext:
            return {"level": "medium", "candidate": ext}

    # L3: broad/ambiguous -> top candidates + retype
    return {"level": "low", "candidates": _google_top_candidate_names_for_query(poi_name, limit=8, exclude_keys=exclude)}


def assess_add_confidence_for_query(poi_name, day_plans=None):
    def _noop_pick(_pois, _target_name):
        return -1, -1

    return _assess_add_target_confidence(
        [],
        poi_name,
        _noop_pick,
        day_plans=day_plans,
    )


def preparse_add_candidate_gate(parsed, pool, day_plans, pick_poi_match):
    parsed = parsed if isinstance(parsed, dict) else {}
    if str(parsed.get("intent") or "").strip().lower() != "add_poi":
        return parsed
    constraints = parsed.get("constraints") if isinstance(parsed.get("constraints"), dict) else {}
    candidate_selected = bool(constraints.get("candidate_selected"))
    selected_from_clar = bool(constraints.get("candidate_selected_from_clarification"))
    # Never trust free-form parser output for candidate_selected.
    # Only skip confidence gate when selection came from explicit clarification choice.
    if candidate_selected and selected_from_clar:
        return parsed
    poi_name = str(parsed.get("poi_name") or "").strip()
    if not poi_name:
        return parsed
    ctype = str(parsed.get("clarification_type") or "").strip()
    if ctype in (
        "choose_replace_target_poi",
        "reenter_add_poi_name",
        "confirm_add_candidate_yes_no",
        "choose_add_candidate_from_list",
    ):
        return parsed

    conf = _assess_add_target_confidence(pool or [], poi_name, pick_poi_match, day_plans=day_plans)
    level = conf.get("level")
    if level == "medium":
        cand = conf.get("candidate") or {}
        cand_name = str((cand or {}).get("name") or poi_name).strip()
        parsed["needs_clarification"] = True
        parsed["clarification_type"] = "confirm_add_candidate_yes_no"
        parsed["clarification_options"] = ["confirm_yes", "confirm_no"]
        parsed["clarification_question"] = f"Did you mean '{cand_name}'?"
        parsed["constraints"] = dict(parsed.get("constraints") or {})
        parsed["constraints"]["proposed_poi_name"] = cand_name
        return parsed
    if level == "low":
        options = list(conf.get("candidates") or [])
        if "type_again" not in options:
            options.append("type_again")
        parsed["needs_clarification"] = True
        parsed["clarification_type"] = "choose_add_candidate_from_list"
        parsed["clarification_options"] = options
        parsed["clarification_question"] = (
            f"'{poi_name}' is too broad or unclear. Please choose one from the top matches or type again."
        )
        return parsed
    return parsed


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

    constraints = parsed.get("constraints") if isinstance(parsed.get("constraints"), dict) else {}
    effective_poi_name = str((constraints or {}).get("confirmed_poi_name") or poi_name or "").strip()
    force_selected = bool((constraints or {}).get("candidate_selected")) and bool(
        (constraints or {}).get("candidate_selected_from_clarification")
    )
    if not force_selected:
        conf = _assess_add_target_confidence(pool, effective_poi_name, pick_poi_match, day_plans=day_plans)
        level = conf.get("level")
        if level == "medium":
            cand = conf.get("candidate") or {}
            cand_name = str((cand or {}).get("name") or effective_poi_name).strip()
            parsed["needs_clarification"] = True
            parsed["clarification_type"] = "confirm_add_candidate_yes_no"
            parsed["clarification_options"] = ["confirm_yes", "confirm_no"]
            parsed["clarification_question"] = f"Did you mean '{cand_name}'?"
            parsed["constraints"] = dict(parsed.get("constraints") or {})
            parsed["constraints"]["proposed_poi_name"] = cand_name
            return {
                "kind": "clarify",
                "question": parsed["clarification_question"],
                "clarification_type": parsed["clarification_type"],
                "clarification_options": parsed["clarification_options"],
                "parsed": parsed,
            }
        if level == "low":
            options = list(conf.get("candidates") or [])
            if "type_again" not in options:
                options.append("type_again")
            parsed["needs_clarification"] = True
            parsed["clarification_type"] = "choose_add_candidate_from_list"
            parsed["clarification_options"] = options
            parsed["clarification_question"] = (
                f"'{effective_poi_name}' is too broad or unclear. Please choose one from the top matches or type again."
            )
            return {
                "kind": "clarify",
                "question": parsed["clarification_question"],
                "clarification_type": parsed["clarification_type"],
                "clarification_options": parsed["clarification_options"],
                "parsed": parsed,
            }

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

    target_poi = resolve_target_poi_from_pool(pool, effective_poi_name, pick_poi_match)
    if not target_poi:
        return {
            "kind": "clarify",
            "question": f"I couldn't confidently find '{effective_poi_name}' in candidate POIs. Please provide a fuller POI name.",
            "parsed": parsed,
        }

    target_uid = poi_uid(target_poi)
    for dp in day_plans:
        for p in (dp.get("pois") or []):
            if target_uid and poi_uid(p) == target_uid:
                return {"kind": "error", "status": 400, "message": f"'{target_poi.get('name') or effective_poi_name}' is already in your itinerary."}

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
