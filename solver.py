"""
MediShift AI Pro — CP-SAT Solver (OR-Tools)
============================================
REST API: POST /solve  →  { schedule, stats, status }

Vstup (JSON):
  nurses  : [{ name, wp, uvazek, noc, prevMonth, nurseRules }]
  rules   : [{ active, type, condition:{nurse,shift,dow,after} }]
  reqs    : { "Jméno": { "1":"CD", "5":"X", ... } }   (den jako string)
  month   : 1–12
  year    : 2024–…
  params  : { minN, minCD, maxShiftsPerWeek, ... }   (volitelné přepsání)

Výstup (JSON):
  schedule: { "Jméno": ["CD","N","","X",...] }  (pole délky days)
  stats   : { solved, wallTime, numVars, numConstraints, objective }
  status  : "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "ERROR"
  message : string (jen u chyby)
"""

from __future__ import annotations

import calendar
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Any

try:
    from flask import Flask, request, jsonify
    from flask_cors import CORS
    FLASK_OK = True
except ImportError:
    FLASK_OK = False

try:
    from ortools.sat.python import cp_model
    ORTOOLS_OK = True
except ImportError:
    ORTOOLS_OK = False


# ── Konstanty ────────────────────────────────────────────────────────────────

SHIFTS       = ['CD', 'N', 'R', 'dov', 'X', '/']
WORK_SHIFTS  = {'CD', 'N', 'R'}          # směny = přítomnost
NIGHT_SHIFTS = {'N'}                      # noční
DAY_SHIFTS   = {'CD'}

DOW_NAMES    = {0:'mo', 1:'tu', 2:'we', 3:'th', 4:'fr', 5:'sa', 6:'su'}


# ── Datové třídy ─────────────────────────────────────────────────────────────

@dataclass
class Nurse:
    name:       str
    wp:         str   = 'A'      # skupina: A / B
    uvazek:     float = 1.0      # 0.5, 0.75, 1.0
    noc:        bool  = True     # může dělat noční
    prevMonth:  str   = ''       # NN / 1N / CDCD / 1CD / ''
    nurseRules: list  = field(default_factory=list)


@dataclass
class Rule:
    active:    bool
    rtype:     str           # ban / must / norm / warning / error
    condition: dict          # {nurse, shift, dow, after}


# ── Parsování vstupu ─────────────────────────────────────────────────────────

def parse_nurses(raw: list) -> list[Nurse]:
    out = []
    for n in raw:
        out.append(Nurse(
            name      = n['name'],
            wp        = n.get('wp', 'A'),
            uvazek    = float(n.get('uvazek', 1.0)),
            noc       = bool(n.get('noc', True)),
            prevMonth = n.get('prevMonth', ''),
            nurseRules= n.get('nurseRules', []),
        ))
    return out


def parse_rules(raw: list) -> list[Rule]:
    out = []
    for r in raw:
        if not r.get('active', True):
            continue
        out.append(Rule(
            active    = True,
            rtype     = r.get('type', 'norm'),
            condition = r.get('condition', {}),
        ))
    return out


# ── Pomocné funkce ────────────────────────────────────────────────────────────

def days_in_month(month: int, year: int) -> int:
    return calendar.monthrange(year, month)[1]


def dow(day: int, month: int, year: int) -> int:
    """Vrátí den týdne: 0=Po … 6=Ne"""
    return date(year, month, day).weekday()


def is_weekend(day: int, month: int, year: int) -> bool:
    return dow(day, month, year) >= 5


def start_type_of(nurse: Nurse) -> str:
    """Výchozí typ víkendu pro tento měsíc dle konce minulého měsíce."""
    pm = nurse.prevMonth
    if pm in ('NN', '1N'):   return 'CD'
    if pm in ('CDCD', '1CD'): return 'N'
    return 'CD' if nurse.wp == 'B' else 'N'


def expected_type(nurse: Nurse, k: int) -> str:
    """Očekávaný typ pro k-tý víkend (k = index 0,1,2,…)."""
    st = start_type_of(nurse)
    return st if k % 2 == 0 else ('CD' if st == 'N' else 'N')


def dow_matches(dow_cond: Any, d: int, month: int, year: int) -> bool:
    """Zkontroluj zda den d splňuje podmínku dow (číslo 0-6, 'we', 'wd', None)."""
    if dow_cond is None or dow_cond == '':
        return True
    dw = dow(d, month, year)
    is_we = dw >= 5
    if dow_cond == 'we':  return is_we
    if dow_cond == 'wd':  return not is_we
    try:
        return dw == int(dow_cond)
    except (TypeError, ValueError):
        return True


# ── CP-SAT Solver ─────────────────────────────────────────────────────────────

def solve(payload: dict) -> dict:
    if not ORTOOLS_OK:
        return {'status': 'ERROR', 'message': 'ortools není nainstalován (pip install ortools)'}

    month   = int(payload['month'])
    year    = int(payload['year'])
    DAYS    = days_in_month(month, year)
    params  = payload.get('params', {})

    nurses  = parse_nurses(payload.get('nurses', []))
    g_rules = parse_rules(payload.get('rules', []))
    reqs    = payload.get('reqs', {})   # {name: {day_str: shift}}

    # Výchozí minimální počty na den
    MIN_N   = int(params.get('minN',  5))
    MIN_CD  = int(params.get('minCD', 5))
    # Maximální pracovní dny za týden (standard 5 z 7 pro full-time)
    MAX_WORK_WEEK = int(params.get('maxWorkWeek', 4))   # 12h směny → ~4/týden

    model  = cp_model.CpModel()
    solver = cp_model.CpSolver()

    # ── Proměnné ──────────────────────────────────────────────────────────────
    # x[n][d][s] = 1 pokud sestra n pracuje den d (1-based) ve směně s
    # Volné dny = žádná WORK směna → modelujeme jako "žádná proměnná = 1"
    # Zjednodušení: pro každou sestru a den = jedna proměnná na každou směnu

    SI = {s: i for i, s in enumerate(SHIFTS)}   # shift → index
    x  = {}   # (nurse_name, day, shift) → BoolVar

    nurse_map = {n.name: n for n in nurses}

    for n in nurses:
        nm = n.name
        req = reqs.get(nm, {})
        for d in range(1, DAYS + 1):
            forced = req.get(str(d), '')
            for s in SHIFTS:
                var = model.NewBoolVar(f'x_{nm}_{d}_{s}')
                x[(nm, d, s)] = var
                # Pokud je požadavek → zafixuj
                if forced:
                    if s == forced:
                        model.Add(var == 1)
                    else:
                        model.Add(var == 0)

    # ── Základní omezení: každý den max jedna směna ────────────────────────
    for n in nurses:
        nm = n.name
        for d in range(1, DAYS + 1):
            model.AddAtMostOne([x[(nm, d, s)] for s in SHIFTS])

    # ── Odpočinek: po noční nemůže být ranní/CD ────────────────────────────
    # N→next day: nesmí být CD nebo R (jen volno, X, dov, nebo N)
    for n in nurses:
        nm = n.name
        for d in range(1, DAYS):
            # Po N: zakaz CD a R následující den
            for forbidden in ('CD', 'R'):
                model.Add(x[(nm, d+1, forbidden)] == 0).OnlyEnforceIf(x[(nm, d, 'N')])

    # ── Pokrytí: min 5 N a min 5 CD každý den ─────────────────────────────
    for d in range(1, DAYS + 1):
        model.Add(sum(x[(n.name, d, 'N')]  for n in nurses) >= MIN_N)
        model.Add(sum(x[(n.name, d, 'CD')] for n in nurses) >= MIN_CD)

    # ── Sestry bez nočních ────────────────────────────────────────────────
    for n in nurses:
        if not n.noc:
            for d in range(1, DAYS + 1):
                model.Add(x[(n.name, d, 'N')] == 0)

    # ── Globální ban/must pravidla ────────────────────────────────────────
    all_rules = [(r, None) for r in g_rules]
    for n in nurses:
        for r_raw in n.nurseRules:
            r = Rule(
                active    = bool(r_raw.get('active', True)),
                rtype     = r_raw.get('type', 'norm'),
                condition = r_raw.get('condition', {}),
            )
            if r.active:
                all_rules.append((r, n.name))

    for (rule, owner_name) in all_rules:
        c = rule.condition
        if not c:
            continue
        c_nurse = c.get('nurse', '')
        c_shift = c.get('shift', '')
        c_dow   = c.get('dow', None)
        c_after = c.get('after', '')

        target_nurses = []
        if c_nurse:
            if c_nurse in nurse_map:
                target_nurses = [nurse_map[c_nurse]]
        elif owner_name:
            target_nurses = [nurse_map[owner_name]] if owner_name in nurse_map else []
        else:
            target_nurses = nurses

        for n in target_nurses:
            nm = n.name
            for d in range(1, DAYS + 1):
                if not dow_matches(c_dow, d, month, year):
                    continue

                if rule.rtype == 'ban' and c_shift:
                    # Zkontroluj podmínku after
                    if c_after and d > 1:
                        prev_shift = None
                        if   c_after == 'N':    prev_shift = 'N'
                        elif c_after == 'CD':   prev_shift = 'CD'
                        elif c_after == 'free': prev_shift = None  # zvláštní případ

                        if prev_shift:
                            # ban platí jen pokud předchozí den = prev_shift
                            model.Add(x[(nm, d, c_shift)] == 0).OnlyEnforceIf(x[(nm, d-1, prev_shift)])
                        elif c_after == 'free':
                            # ban platí jen pokud d-1 = volno (žádná WORK směna)
                            no_work_prev = [x[(nm, d-1, s)].Not() for s in WORK_SHIFTS]
                            b = model.NewBoolVar(f'free_{nm}_{d}')
                            model.AddBoolAnd(no_work_prev).OnlyEnforceIf(b)
                            model.Add(x[(nm, d, c_shift)] == 0).OnlyEnforceIf(b)
                    else:
                        model.Add(x[(nm, d, c_shift)] == 0)

                elif rule.rtype == 'must' and c_shift:
                    req_d = reqs.get(nm, {}).get(str(d), '')
                    if not req_d:  # nevynutit pokud je explicitní požadavek
                        model.Add(x[(nm, d, c_shift)] == 1)

    # ── Maximální pracovní dny za týden ──────────────────────────────────
    # Spočítej pracovní týdny v měsíci
    for n in nurses:
        nm = n.name
        # Každý týden = 7 dní, omez pracovní směny
        # max_work závisí na úvazku
        max_work = round(MAX_WORK_WEEK * n.uvazek)
        week_start = 1
        while week_start <= DAYS:
            week_end = min(week_start + 6, DAYS)
            work_vars = [x[(nm, d, s)] for d in range(week_start, week_end + 1) for s in WORK_SHIFTS]
            model.Add(sum(work_vars) <= max_work)
            week_start += 7

    # ── Víkendy: N/CD střídání dle expectedType ───────────────────────────
    # Najdi víkendové dvojice (so, ne)
    weekends: list[tuple[int, int|None]] = []
    d = 1
    while d <= DAYS:
        dw = dow(d, month, year)
        if dw == 5:   # sobota
            su = d + 1 if d + 1 <= DAYS else None
            weekends.append((d, su))
            d += 2
        else:
            d += 1

    we_count = {n.name: 0 for n in nurses}
    for wi, (sat, sun) in enumerate(weekends):
        # Každá sestra max na jednom víkendu za měsíc (základní pravidlo)
        # Pokud sestra pracuje na víkendu, je to přičteno we_count — ale v CP-SAT
        # tohle modelujeme jako soft-constraint přes expected type.
        pass  # expected type je spíš soft; hard constraint = pokrytí N/CD

    # ── Cíl: minimalizuj neobsazené pracovní dny (max přítomnost) ─────────
    # + penalizuj odchylky od expectedType na víkendech
    penalty_terms = []

    # Soft: víkendový typ dle expectedType
    for n in nurses:
        nm = n.name
        wc = 0
        for sat, sun in weekends:
            exp = expected_type(n, wc)
            good_shift = exp   # 'N' nebo 'CD'
            bad_shifts = [s for s in ('N', 'CD') if s != good_shift]
            for day in ([sat] + ([sun] if sun else [])):
                for bs in bad_shifts:
                    # Penalizuj špatný typ
                    penalty_terms.append(x[(nm, day, bs)])
            wc += 1

    if penalty_terms:
        model.Minimize(sum(penalty_terms))

    # ── Řeš ───────────────────────────────────────────────────────────────
    solver.parameters.max_time_in_seconds = float(params.get('timeLimit', 30))
    solver.parameters.num_search_workers  = int(params.get('workers', 4))

    status = solver.Solve(model)

    status_map = {
        cp_model.OPTIMAL:    'OPTIMAL',
        cp_model.FEASIBLE:   'FEASIBLE',
        cp_model.INFEASIBLE: 'INFEASIBLE',
        cp_model.UNKNOWN:    'UNKNOWN',
    }
    status_str = status_map.get(status, 'ERROR')

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = {}
        for n in nurses:
            nm = n.name
            row = []
            for d in range(1, DAYS + 1):
                assigned = ''
                for s in SHIFTS:
                    if solver.Value(x[(nm, d, s)]):
                        assigned = s
                        break
                row.append(assigned)
            schedule[nm] = row

        return {
            'status':   status_str,
            'schedule': schedule,
            'stats': {
                'wallTime':        round(solver.WallTime(), 3),
                'numVars':         solver.NumBooleans(),
                'numConstraints':  len(model.Proto().constraints),
                'objective':       solver.ObjectiveValue(),
                'solved':          True,
            }
        }
    else:
        return {
            'status':  status_str,
            'message': f'Solver skončil se stavem {status_str}. Zkontroluj omezení (min. pokrytí, ban pravidla).',
            'stats':   {'solved': False, 'wallTime': round(solver.WallTime(), 3)},
        }


# ── Flask API ─────────────────────────────────────────────────────────────────

if FLASK_OK:
    app = Flask(__name__)
    CORS(app)

    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({'ok': True, 'ortools': ORTOOLS_OK})

    @app.route('/solve', methods=['POST'])
    def solve_endpoint():
        try:
            payload = request.get_json(force=True)
            result  = solve(payload)
            return jsonify(result)
        except Exception as e:
            return jsonify({'status': 'ERROR', 'message': str(e)}), 500

    def run_server(host='127.0.0.1', port=5055, debug=False):
        print(f'MediShift solver běží na http://{host}:{port}')
        app.run(host=host, port=port, debug=debug)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if not FLASK_OK:
        print('CHYBA: Nainstaluj Flask a flask-cors:  pip install flask flask-cors ortools')
        sys.exit(1)
    if not ORTOOLS_OK:
        print('CHYBA: Nainstaluj OR-Tools:  pip install ortools')
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description='MediShift CP-SAT solver server')
    parser.add_argument('--host',  default='127.0.0.1')
    parser.add_argument('--port',  default=5055, type=int)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--test',  action='store_true', help='Spustí testovací výpočet a skončí')
    args = parser.parse_args()

    if args.test:
        # Minimalní test
        test_payload = {
            'month': 4, 'year': 2026,
            # 24 sester: 12 A-skupina + 12 B-skupina
            'nurses': [
                {'name': f'Sestra{i}', 'wp': 'A' if i <= 12 else 'B',
                 'uvazek': 1.0, 'noc': i % 3 != 0, 'prevMonth': 'NN' if i % 2 == 0 else 'CDCD'}
                for i in range(1, 25)
            ],
            'rules': [],
            'reqs':  {},
            'params': {'timeLimit': 15, 'workers': 2, 'minN': 5, 'minCD': 5},
        }
        result = solve(test_payload)
        print(json.dumps({k: v for k, v in result.items() if k != 'schedule'}, indent=2))
        if result.get('schedule'):
            first = next(iter(result['schedule'].items()))
            print(f'Ukázka ({first[0]}): {first[1][:10]}…')
        sys.exit(0)

    run_server(host=args.host, port=args.port, debug=args.debug)
