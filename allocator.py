"""
Transparent, rule-based allocation algorithm for the Emergency Resource Manager.

It never decides anything by itself: it only RECOMMENDS. Every recommendation carries
a list of human-readable reasons ("why") so staff can verify and reject it.

Rules
-----
1. Priority score = category weight + waiting bonus + age-risk bonus
     waiting bonus  = 0.5 points per minute waited (capped at 60 minutes)
     age-risk bonus = +5 for children under 5 and adults over 65
2. Cases are served in descending score order (greedy allocation).
3. Bed: required ward first; otherwise the nearest HIGHER-care ward
   (ER -> ICU, General -> ER -> ICU). ICU and Isolation never fall back downward/sideways.
4. Doctor: exact specialty match first; otherwise a free Emergency/General doctor.
5. Ambulance: only if the case needs transport; first free ambulance.
6. Resources reserved for a higher-ranked case are not offered to lower-ranked ones.
7. Beds previously rejected by staff for a case are skipped for that case.
"""
import json

ORDER = {
    "ICU": ["ICU"],
    "ER": ["ER", "ICU"],
    "General": ["General", "ER", "ICU"],
    "Isolation": ["Isolation"],
}
FALLBACK_SPECIALTIES = ("Emergency", "General")
AGE_BONUS = 5
WAIT_POINTS_PER_MIN = 0.5
WAIT_CAP_MIN = 60


def score_case(case, cat, now):
    waited = max(0.0, (now - case["created_at"]) / 60)
    wait_bonus = min(waited, WAIT_CAP_MIN) * WAIT_POINTS_PER_MIN
    age_bonus = AGE_BONUS if (case["age"] < 5 or case["age"] > 65) else 0
    total = cat["weight"] + wait_bonus + age_bonus
    text = (f"Score {total:.1f} = category {cat['name']} ({cat['weight']:g}) "
            f"+ waiting time ({wait_bonus:.1f}) + age-risk ({age_bonus})")
    return total, text


def plan(cases, beds, doctors, ambulances, cats, now):
    """Return {case_id: recommendation} for every waiting case."""
    cat_map = {c["id"]: c for c in cats}
    fallback_cat = min(cats, key=lambda c: c["weight"])
    waiting = []
    for c in cases:
        if c["status"] != "waiting":
            continue
        total, text = score_case(c, cat_map.get(c["category_id"], fallback_cat), now)
        waiting.append((total, c, text))
    waiting.sort(key=lambda x: -x[0])

    used_beds, used_docs, used_ambs = set(), set(), set()
    out = {}
    for rank, (total, c, text) in enumerate(waiting, start=1):
        why = [text]
        excluded = json.loads(c.get("excluded") or "[]")

        bed = None
        for ward in ORDER.get(c["need"], [c["need"]]):
            bed = next((b for b in beds if b["ward"] == ward and b["status"] == "free"
                        and b["id"] not in used_beds and b["id"] not in excluded), None)
            if bed:
                break
        if bed:
            used_beds.add(bed["id"])
            why.append(f"Bed {bed['id']}: required ward {c['need']} available" if bed["ward"] == c["need"]
                       else f"No {c['need']} bed free, using nearest higher-care ward {bed['ward']}")
        else:
            why.append(f"No suitable bed free for {c['need']}")

        free_docs = [d for d in doctors if d["status"] == "free" and d["id"] not in used_docs]
        doc = next((d for d in free_docs if d["specialty"] == c["specialty"]), None)
        if doc:
            why.append(f"Specialty match: {c['specialty']}")
        else:
            doc = next((d for d in free_docs if d["specialty"] in FALLBACK_SPECIALTIES), None)
            why.append(f"No free {c['specialty']} specialist, fallback to {doc['specialty']} doctor"
                       if doc else "No doctor free")
        if doc:
            used_docs.add(doc["id"])

        amb = None
        if c["transport"]:
            amb = next((a for a in ambulances if a["status"] == "free" and a["id"] not in used_ambs), None)
            if amb:
                used_ambs.add(amb["id"])
                why.append("First free ambulance assigned")
            else:
                why.append("No ambulance free")

        out[c["id"]] = {
            "rank": rank, "score": round(total, 1), "why": why,
            "bed": {"id": bed["id"], "ward": bed["ward"]} if bed else None,
            "doctor": {"id": doc["id"], "name": doc["name"], "specialty": doc["specialty"]} if doc else None,
            "ambulance": amb["id"] if amb else None,
        }
    return out
