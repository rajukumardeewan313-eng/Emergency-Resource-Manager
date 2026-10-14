# ER Command: Smart Hospital Emergency Resource Manager (HT-03)

Flask + SQLite REST API with a single-page dashboard. No API keys, no external services.
All data is simulated and fictional. Decision support only: staff approve every allocation.

## Run
```
python -m venv venv
venv\Scripts\activate        (Windows)   |   source venv/bin/activate   (Mac/Linux)
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000  (the database `emergency.db` is created and seeded automatically).

## Requirement coverage
| Requirement | Where |
|---|---|
| Register cases with configurable priority categories | `POST /api/cases`, `PUT /api/categories`, "Priority Categories" dialog |
| Available/occupied beds (simulated live data) | `beds` table, `/api/state`, `/api/simulate/tick` |
| Doctor availability and ambulance status | `doctors`, `ambulances` tables, cycle endpoints |
| Recommend allocations by priority and availability | `allocator.py` |
| Dashboard with status updates | `static/index.html` (polls `/api/state` every 3 s), status log |

## Algorithm (allocator.py, fully transparent)
1. score = category weight + 0.5 per minute waited (cap 60 min) + 5 if age < 5 or > 65
2. Cases handled in descending score; resources reserved for higher-ranked cases
3. Bed: required ward, else nearest higher-care ward (ER -> ICU, General -> ER -> ICU)
4. Doctor: exact specialty, else free Emergency/General doctor
5. Ambulance: only if transport needed
6. Staff can Reject (skips that bed and recomputes) or Approve. Every card has "Why this recommendation?"

## REST API
| Method | Path | Purpose |
|---|---|---|
| GET | /api/state | everything + recommendations + stats + log |
| POST | /api/cases | register a case |
| POST | /api/cases/<id>/approve, /reject, /complete | staff decisions |
| POST | /api/beds/<id>/toggle | manual bed status |
| POST | /api/doctors/<id>/cycle, /api/ambulances/<id>/cycle | availability |
| PUT | /api/categories | edit/add priority categories |
| POST | /api/simulate/tick, /api/reset | demo helpers |

## Demo script
1. Show dashboard and the ranked queue; open "Why this recommendation?".
2. Approve the top case; watch bed/doctor/ambulance go busy and stats change.
3. Mark all ICU beds occupied: recommendations change (fallback/"no bed").
4. Reject a recommendation: next-best bed appears.
5. Edit category weights: ranking reorders.
6. Turn Simulation ON for a live feel.

Limits: simulated data; not a clinical triage tool; no authentication (add login for real use).
