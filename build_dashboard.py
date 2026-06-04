"""
Claims & Citas Dashboard — Brio Management
Categorías:
  1. FYBA / Insurance  — NLS task template=2 (INSURANCE)
  2. Garantía          — NLS task template=4 (WARRANTY)
  3. Pérdida Total     — NLS task template=5 (VEHICLE LOSS)
  4. Shopmonkey Otros  — órdenes/citas sin task NLS

Deduplicación: si un VIN tiene task NLS + cita/orden Shopmonkey → 1 caso.
"""

import os, json, time, pyodbc, requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Credenciales ──────────────────────────────────────────────────────────────
SM_TOKEN = os.environ.get("SM_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjaWQiOiIwYTFhZWFmYy04MjdjLTQ4NDItYTE2Zi01MzVmMzUzMzhhZDQiLCJpZCI6ImY3MjI0NGRiLTBmNWEtNDEzYS1hOTk2LTU0NzE1NjFkOTI1YSIsImxpZCI6Ijk5ODhlYjQ2LTYxNmEtNDA0NC04NmJiLTAwNzI3MWJjODZiMyIsInAiOiJhcGkiLCJyaWQiOiJ1YzEiLCJzYWQiOjAsInNpZCI6ImRjZWRmM2Y5YzYxY2YxZDAiLCJ0Y2lkIjoiMGExYWVhZmMtODI3Yy00ODQyLWExNmYtNTM1ZjM1MzM4YWQ0IiwiZGF0YVNoYXJpbmciOmZhbHNlLCJoYXNIcSI6ZmFsc2UsIm9uYiI6NywicGF5Ijo2LCJhdWQiOiJhcGkiLCJpc3MiOiJodHRwczovL2FwaS5zaG9wbW9ua2V5LmNsb3VkIiwiaWF0IjoxNzc4NjE2MzE2LCJleHAiOjQ5MzQzNzYzMTZ9.lx_jaNxw_-mAeEgEswZ2CVQUYuilPNvaxKq-zsx-6zM")
NLS_CONN = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=rs3.nortridgehosting.com;DATABASE=Brio_Management;"
    "UID=Bmrs8800;"
    f"PWD={os.environ.get('NLS_PWD','!04#c@d629')};"
    "TrustServerCertificate=yes;"
)
SM_BASE = "https://api.shopmonkey.cloud/v3"
SM_HDR  = {"Authorization": f"Bearer {SM_TOKEN}"}
ET      = timezone(timedelta(hours=-4))
ENG_KW  = ['engine','motor','transmis','long block','short block','transaxle','cvt','rebuilt']

# ── HTTP helper ───────────────────────────────────────────────────────────────
def sm_get(path, retries=5):
    url = SM_BASE + path
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=SM_HDR, timeout=30)
            if r.status_code == 429:
                wait = 30 * (2 ** attempt)  # 30, 60, 120, 240, 480 seg
                print(f"  Rate limit 429, waiting {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                print(f"  WARN sm_get {path}: {e}")
                return None

def parse_date(raw):
    if not raw:
        return "", ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m")
    except Exception:
        return raw[:10], raw[:7]

# ── NLS: fetch tasks ──────────────────────────────────────────────────────────
def fetch_nls_tasks():
    print("NLS: fetching tasks...")
    conn = pyodbc.connect(NLS_CONN)
    cur = conn.cursor()
    cur.execute("""
        SELECT
            t.task_refno,
            t.task_template_no,
            tt.task_template_name,
            t.subject,
            t.creation_date,
            t.completion_date,
            tsc.status_code,
            CAST(l.loan_number AS VARCHAR(20)) AS loan_number,
            l.name AS client,
            cv.vin,
            COALESCE(lpc.portfolio_description,'') AS portfolio,
            t.notes
        FROM task t
        JOIN task_template tt ON tt.task_template_no = t.task_template_no
        LEFT JOIN task_status_codes tsc ON tsc.status_code_id = t.status_code_id
        LEFT JOIN loanacct l ON l.acctrefno = t.NLS_refno
        LEFT JOIN loanacct_collateral_link lcl ON lcl.acctrefno = l.acctrefno
        LEFT JOIN collateral_vehicle cv ON cv.collateral_id = lcl.collateral_id
        LEFT JOIN loan_port_codes lpc ON lpc.portfolio_code_id = l.portfolio_code_id
        WHERE t.task_template_no IN (2, 4, 5)
        ORDER BY t.creation_date DESC
    """)
    tasks = []
    for row in cur.fetchall():
        refno, tmpl_no, tmpl_name, subject, created, completed, status_code, loan, client, vin, portfolio, notes = row
        is_open = completed is None
        created_str  = created.strftime("%Y-%m-%d")  if created  else ""
        created_mon  = created.strftime("%Y-%m")     if created  else ""
        completed_str= completed.strftime("%Y-%m-%d") if completed else ""
        days_open    = (datetime.now() - created).days if created and is_open else (
                       (completed - created).days if created and completed else 0)
        category  = {2: "Insurance Claim (FYBA)", 4: "Garantía", 5: "Pérdida Total"}.get(int(tmpl_no), "Otro")
        # Garantía: facturado a la cartera correspondiente (Line Capital / DCJ / etc.)
        if int(tmpl_no) == 4:
            billed_to = portfolio if portfolio else "Brio Management"
        elif int(tmpl_no) == 2:
            billed_to = "FYBA Reinsurance"
        elif int(tmpl_no) == 5:
            billed_to = "Seguro / Total Loss"
        else:
            billed_to = ""
        tasks.append({
            "task_refno":   int(refno),
            "category":     category,
            "billed_to":    billed_to,
            "subject":      subject or "",
            "created":      created_str,
            "created_mon":  created_mon,
            "completed":    completed_str,
            "is_open":      is_open,
            "status":       status_code or "",
            "days_open":    days_open,
            "loan":         loan or "",
            "client":       client or "",
            "vin":          (vin or "").upper().strip(),
            "portfolio":    portfolio,
            "notes":        (notes or "")[:200],
        })
    conn.close()
    print(f"NLS tasks: {len(tasks)}")
    return tasks

# ── NLS: historial de loans por VIN (con fechas) ─────────────────────────────
def fetch_nls_vin_history(vins):
    """
    Retorna {vin: [loan_record, ...]} donde cada registro tiene open_date y closed_date.
    Permite encontrar el loan ACTIVO en la fecha de un trabajo Shopmonkey.
    Excluye duplicados LINE CAPITAL / LENDERIN (mismo deal, dos portafolios):
    cuando hay dos loans con misma open_date para el mismo VIN, prefiere el no-Lenderin.
    """
    if not vins:
        return {}
    conn = pyodbc.connect(NLS_CONN)
    cur  = conn.cursor()
    placeholders = ",".join(["?"] * len(vins))
    cur.execute(f"""
        SELECT
            cv.vin,
            l.acctrefno,
            CAST(l.loan_number AS VARCHAR(20)) AS loan_number,
            l.name,
            COALESCE(lpc.portfolio_description,'') AS portfolio,
            l.portfolio_code_id,
            l.status_code_no,
            l.open_date,
            l.closed_date
        FROM collateral_vehicle cv
        JOIN loanacct_collateral_link lcl ON lcl.collateral_id = cv.collateral_id
        JOIN loanacct l ON l.acctrefno = lcl.acctrefno
        LEFT JOIN loan_port_codes lpc ON lpc.portfolio_code_id = l.portfolio_code_id
        WHERE cv.vin IN ({placeholders})
        ORDER BY cv.vin, l.open_date, l.acctrefno
    """, list(vins))

    history = {}
    for row in cur.fetchall():
        vin, acctrefno, loan_num, name, portfolio, port_id, status, open_dt, closed_dt = row
        vin = vin.upper().strip()
        if vin not in history:
            history[vin] = []
        history[vin].append({
            "acctrefno": int(acctrefno),
            "loan":      loan_num or "",
            "name":      name or "",
            "portfolio": portfolio,
            "port_id":   int(port_id) if port_id else 0,
            "status":    int(status) if status else 0,
            "open_date": open_dt,
            "closed_date": closed_dt,
        })

    # Deduplicar: si hay dos loans con misma open_date para el mismo VIN,
    # preferir el que NO sea Lenderin (port_id=4) — son el mismo deal registrado dos veces
    LENDERIN_ID = 4
    for vin in history:
        deduped = []
        by_open = {}
        for rec in history[vin]:
            od = str(rec["open_date"])[:10] if rec["open_date"] else "none"
            if od not in by_open:
                by_open[od] = rec
            else:
                existing = by_open[od]
                # Preferir el no-Lenderin
                if existing["port_id"] == LENDERIN_ID and rec["port_id"] != LENDERIN_ID:
                    by_open[od] = rec
        history[vin] = sorted(by_open.values(), key=lambda r: r["open_date"] or datetime.min)

    conn.close()
    print(f"NLS VIN history: {len(history)} VINs")
    return history


def find_loan_at_date(vin_history, vin, work_date_str):
    """
    Dado un VIN y una fecha de trabajo, devuelve el loan NLS que estaba activo en esa fecha.
    Regla: open_date <= work_date AND (closed_date IS NULL OR closed_date >= work_date)
    Fallback: el loan más reciente (por si no hay match exacto de fechas).
    """
    records = vin_history.get(vin, [])
    if not records:
        return None
    try:
        work_dt = datetime.strptime(work_date_str[:10], "%Y-%m-%d")
    except Exception:
        return records[-1]  # fallback: más reciente

    candidates = []
    for rec in records:
        open_dt   = rec["open_date"]
        closed_dt = rec["closed_date"]
        if open_dt and open_dt.replace(tzinfo=None) <= work_dt:
            if closed_dt is None or closed_dt.replace(tzinfo=None) >= work_dt:
                candidates.append(rec)

    if candidates:
        # Si hay varios activos en esa fecha (raro), tomar el más reciente
        return max(candidates, key=lambda r: r["open_date"])
    # Fallback: el loan más reciente con open_date <= work_date
    past = [r for r in records if r["open_date"] and r["open_date"].replace(tzinfo=None) <= work_dt]
    if past:
        return max(past, key=lambda r: r["open_date"])
    return records[0]  # último recurso


REPO_CODES = (10, 11, 12, 18, 28)  # REPOSSESSED, OUT FOR REPO, REPO REQUESTED, REPO & SOLD, REPO IN DEALER

def fetch_nls_repo_vins():
    """VINs con cualquier status de repo (primario o secundario) en NLS."""
    conn = pyodbc.connect(NLS_CONN)
    cur  = conn.cursor()
    codes_str = ",".join(str(c) for c in REPO_CODES)
    cur.execute(f"""
        SELECT DISTINCT cv.vin
        FROM collateral_vehicle cv
        JOIN loanacct_collateral_link lcl ON lcl.collateral_id = cv.collateral_id
        JOIN loanacct l ON l.acctrefno = lcl.acctrefno
        WHERE l.status_code_no IN ({codes_str})
           OR l.acctrefno IN (
               SELECT acctrefno FROM loanacct_statuses
               WHERE status_code_no IN ({codes_str})
           )
    """)
    vins = set(row[0].upper().strip() for row in cur.fetchall() if row[0])
    conn.close()
    print(f"NLS repo VINs: {len(vins)}")
    return vins

# ── Shopmonkey: fetch orders ──────────────────────────────────────────────────
def fetch_orders():
    print("SM: fetching orders...")
    seen, orders, empty_streak, offset = set(), [], 0, 0
    while True:
        data = sm_get(f"/order?limit=100&offset={offset}&sort=number&sortDir=asc")
        if not data:
            break
        batch = data.get("data", [])
        new = [o for o in batch if o["id"] not in seen]
        for o in new:
            seen.add(o["id"])
            orders.append(o)
        if len(new) == 0:
            empty_streak += 1
            if empty_streak >= 6:
                break
        else:
            empty_streak = 0
        offset += 100
    print(f"SM orders: {len(orders)}")
    return orders

# ── Shopmonkey: fetch appointments ────────────────────────────────────────────
def fetch_appointments():
    print("SM: fetching appointments...")
    appts, offset = [], 0
    while True:
        data = sm_get(f"/appointment?limit=100&offset={offset}")
        if not data:
            break
        batch = data.get("data", [])
        if not batch:
            break
        appts.extend(batch)
        if not data.get("hasMore"):
            break
        offset += 100
    print(f"SM appointments: {len(appts)}")
    return appts

# ── Shopmonkey: fetch VINs ────────────────────────────────────────────────────
def fetch_vins(vehicle_ids):
    print(f"SM: fetching VINs ({len(vehicle_ids)} vehicles)...")
    vid_to_vin = {}
    done = [0]
    def get_vin(vid):
        data = sm_get(f"/vehicle/{vid}")
        if data:
            v = data.get("data", {})
            vin = (v.get("vin") or "").upper().strip()
            if len(vin) >= 6:
                return vid, vin
        return vid, None
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(get_vin, vid): vid for vid in vehicle_ids}
        for fut in as_completed(futs):
            vid, vin = fut.result()
            if vin:
                vid_to_vin[vid] = vin
            done[0] += 1
            if done[0] % 100 == 0:
                print(f"  VINs: {done[0]}/{len(vehicle_ids)}")
                time.sleep(2)  # pequeña pausa cada 100 calls
    print(f"VINs fetched: {len(vid_to_vin)}")
    return vid_to_vin

# ── Shopmonkey: fetch services ────────────────────────────────────────────────
def fetch_services(order_ids):
    print(f"SM: fetching services ({len(order_ids)} orders)...")
    result = {}
    done = [0]
    def get_svc(oid):
        data = sm_get(f"/order/{oid}/service")
        if not data:
            return oid, []
        svcs = []
        for s in data.get("data", []):
            name = s.get("name", "")
            cost = (s.get("totalCostCents") or 0) / 100
            labors = [lb.get("name","") for lb in (s.get("labors") or []) if lb.get("name")]
            desc   = "; ".join(labors) or name
            is_et  = any(k in (name+desc).lower() for k in ENG_KW)
            svcs.append({"name": name, "desc": desc, "cost": cost, "is_et": is_et})
        return oid, svcs
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(get_svc, oid): oid for oid in order_ids}
        for fut in as_completed(futs):
            oid, svcs = fut.result()
            result[oid] = svcs
            done[0] += 1
            if done[0] % 100 == 0:
                print(f"  Services: {done[0]}/{len(order_ids)}")
                time.sleep(3)  # pausa cada 100 calls
    print("Services done")
    return result

# ── Build unified case records ────────────────────────────────────────────────
def sm_client_type(name):
    """
    Analiza el nombre del cliente en Shopmonkey para determinar quién paga.
    Retorna: 'fyba' | 'supercar' | 'client' (cliente Brio / individual)
    """
    n = (name or "").upper()
    if "FYBA" in n:
        return "fyba"
    if "SUPER CAR" in n or "SUPERCAR" in n:
        return "supercar"
    return "client"

def build_cases(nls_tasks, orders, appts, vid_to_vin, services_map, repo_vins, vin_history):
    """
    Construye casos unificados.
    - NLS tasks son la fuente principal (1 caso por task).
    - SM orders adjuntos a un task se filtran por fecha del task (para evitar
      asociar trabajo de otro período/loan al mismo VIN).
    - SM-only records (sin task NLS): se busca el loan activo en la fecha del trabajo.
    """

    # ── Indexar órdenes SM por VIN ────────────────────────────────────────────
    vin_to_orders = {}
    for o in orders:
        vid = o.get("vehicleId") or ""
        vin = vid_to_vin.get(vid, "")
        if not vin:
            vin = "__no_vin__" + o["id"]
        svcs       = services_map.get(o["id"], [])
        total      = (o.get("totalCostCents") or 0) / 100
        if total == 0:
            total  = sum(s["cost"] for s in svcs)
        et_cost    = sum(s["cost"] for s in svcs if s["is_et"])
        is_et      = any(s["is_et"] for s in svcs)
        svc_desc   = " | ".join(s["name"] for s in svcs[:4] if s["name"])
        order_type = o.get("orderType", o.get("type",""))
        is_open    = order_type in ("RepairOrder", "Estimate")
        date_str, mon = parse_date(o.get("createdDate") or o.get("date",""))
        rec = {
            "sm_id": o["id"], "sm_number": o.get("number",""),
            "sm_name": o.get("coalescedName",""), "date": date_str, "month": mon,
            "total": total, "et_cost": et_cost, "is_et": is_et,
            "is_open": is_open, "order_type": order_type, "desc": svc_desc,
        }
        if vin not in vin_to_orders:
            vin_to_orders[vin] = []
        vin_to_orders[vin].append(rec)

    # ── Indexar citas SM por VIN (dedup: prioridad Confirmed > NoResponse > Declined) ──
    vin_to_appts = {}
    for a in appts:
        vid = a.get("vehicleId") or ""
        vin = vid_to_vin.get(vid, "")
        if not vin:
            continue
        date_str, mon = parse_date(a.get("date") or a.get("scheduledDate",""))
        conf = a.get("confirmationStatus","NoResponse")
        rec  = {"name": a.get("name",""), "date": date_str, "month": mon,
                "note": a.get("note","") or "", "status": conf}
        if vin not in vin_to_appts:
            vin_to_appts[vin] = rec
        else:
            prio = {"Confirmed":0,"NoResponse":1,"Declined":2}
            if prio.get(conf,9) < prio.get(vin_to_appts[vin]["status"],9):
                vin_to_appts[vin] = rec

    # VINs que tienen task NLS
    task_vins = set(t["vin"] for t in nls_tasks if t["vin"])

    def sm_orders_in_window(vin, start_str, end_str):
        """Órdenes SM del VIN dentro del período [start, end] del task."""
        all_o = vin_to_orders.get(vin, [])
        if not start_str:
            return all_o
        try:
            start = datetime.strptime(start_str[:10], "%Y-%m-%d")
            end   = datetime.strptime(end_str[:10], "%Y-%m-%d") if end_str else datetime.now()
        except Exception:
            return all_o
        return [o for o in all_o if o["date"] and start <= datetime.strptime(o["date"], "%Y-%m-%d") <= end]

    # ── Casos desde NLS tasks ─────────────────────────────────────────────────
    cases = []
    for t in nls_tasks:
        vin = t["vin"]
        # Solo órdenes SM dentro del período del task
        sm_orders = sm_orders_in_window(vin, t["created"], t["completed"] or "")
        sm_appt   = vin_to_appts.get(vin)
        # Filtrar cita por período del task también
        if sm_appt and sm_appt["date"] and t["created"]:
            try:
                appt_dt = datetime.strptime(sm_appt["date"], "%Y-%m-%d")
                task_start = datetime.strptime(t["created"][:10], "%Y-%m-%d")
                task_end   = datetime.strptime(t["completed"][:10], "%Y-%m-%d") if t["completed"] else datetime.now()
                if not (task_start <= appt_dt <= task_end):
                    sm_appt = None
            except Exception:
                pass

        sm_cost   = sum(o["total"] for o in sm_orders)
        sm_et     = sum(o["et_cost"] for o in sm_orders)
        sm_open   = any(o["is_open"] for o in sm_orders)
        sm_desc   = " | ".join(o["desc"] for o in sm_orders[:2] if o["desc"])
        sm_dates  = sorted(o["date"] for o in sm_orders if o["date"])
        sm_first  = sm_dates[0] if sm_dates else ""
        sm_last   = sm_dates[-1] if sm_dates else ""
        appt_note   = sm_appt["note"][:120] if sm_appt else ""
        appt_status = sm_appt["status"] if sm_appt else ""

        cases.append({
            "source":     "NLS",
            "category":   t["category"],
            "billed_to":  t["billed_to"],
            "task_refno": t["task_refno"],
            "subject":    t["subject"],
            "client":     t["client"] or (sm_appt["name"] if sm_appt else ""),
            "loan":       t["loan"],
            "vin":        vin,
            "portfolio":  t["portfolio"],
            "nls_open":   t["is_open"],
            "status":     t["status"],
            "days_open":  t["days_open"],
            "created":    t["created"],
            "created_mon":t["created_mon"],
            "completed":  t["completed"],
            "notes":      t["notes"],
            # Shopmonkey linked data
            "sm_orders":  len(sm_orders),
            "sm_cost":    sm_cost,
            "sm_et":      sm_et,
            "sm_open":    sm_open,
            "sm_desc":    sm_desc[:120],
            "sm_first":   sm_first,
            "sm_last":    sm_last,
            "appt_note":  appt_note,
            "appt_status":appt_status,
        })

    # Shopmonkey records with NO NLS task — clasificar y buscar loan activo por fecha
    for vin, sm_orders_list in vin_to_orders.items():
        if vin.startswith("__no_vin__") or vin in task_vins:
            continue
        sm_cost    = sum(o["total"] for o in sm_orders_list)
        sm_et      = sum(o["et_cost"] for o in sm_orders_list)
        sm_open    = any(o["is_open"] for o in sm_orders_list)
        sm_desc    = " | ".join(o["desc"] for o in sm_orders_list[:2] if o["desc"])
        sm_dates   = sorted(o["date"] for o in sm_orders_list if o["date"])
        first_date = sm_dates[0] if sm_dates else ""
        last_date  = sm_dates[-1] if sm_dates else ""
        mon        = first_date[:7] if first_date else ""
        appt       = vin_to_appts.get(vin)
        appt_name  = appt["name"] if appt else ""
        client_sm  = sm_orders_list[0]["sm_name"] if sm_orders_list else ""
        ctype      = sm_client_type(appt_name) if appt else sm_client_type(client_sm)

        has_appt  = appt is not None
        ref_date  = appt["date"] if appt and appt["date"] else first_date

        # Buscar el loan NLS activo en la fecha del trabajo SM
        nls_loan    = find_loan_at_date(vin_history, vin, ref_date)
        vin_in_nls  = vin in vin_history   # VIN estuvo alguna vez en cartera Brio
        vin_in_repo = vin in repo_vins     # VIN tiene status repo en NLS

        # Clasificación por orden de prioridad:
        # 1. FYBA como cliente en SM → siempre es repo recon (FYBA paga)
        # 2. SuperCar como cliente → dealer prep
        # 3. Cita de cliente individual → Garantía (regla: si hay cita, es nuestro)
        # 4. Sin cita + VIN en repo NLS → Repo Reacondicionamiento
        # 5. Sin cita + VIN en NLS pero sin loan activo en esa fecha → gap entre loans = Repo Recon
        # 6. Sin cita + VIN en NLS con loan activo → Garantía sin cita (poco frecuente)
        # 7. VIN no está en NLS → Externo (ajeno a Brio)
        if ctype == "fyba":
            category  = "Repo / Reacondicionamiento"
            billed_to = "FYBA Reinsurance"
        elif ctype == "supercar":
            category  = "Dealer (SuperCar)"
            billed_to = "Super Car Miami Group LLC"
        elif has_appt and ctype == "client":
            category  = "Garantía"
            billed_to = "Brio Management (Garantía)"
        elif vin_in_repo or (vin_in_nls and nls_loan is None):
            # VIN en repo status actual O estuvo en NLS pero sin loan activo en la fecha
            # (gap entre loans = período de reacondicionamiento entre un repo y el siguiente owner)
            category  = "Repo / Reacondicionamiento"
            billed_to = "FYBA Reinsurance"
        elif vin_in_nls and nls_loan is not None:
            # Loan activo en NLS pero sin cita → garantía sin cita (poco frecuente)
            category  = "Garantía"
            billed_to = nls_loan.get("portfolio", "Brio Management")
        else:
            # VIN no está en NLS en absoluto → completamente externo
            category  = "Externo"
            billed_to = "—"
        loan_num  = nls_loan["loan"]      if nls_loan else ""
        client    = nls_loan["name"]      if nls_loan else (appt_name or client_sm)
        portfolio = nls_loan["portfolio"] if nls_loan else ""

        # Para Garantía: verificar que el loan estuviera abierto en la fecha del trabajo
        # Si closed_date < ref_date → el loan ya estaba cerrado → marcar como dudoso
        loan_valid = True
        if nls_loan and category == "Garantía":
            closed = nls_loan.get("closed_date")
            try:
                ref_dt = datetime.strptime(ref_date[:10], "%Y-%m-%d")
                if closed and closed.replace(tzinfo=None) < ref_dt:
                    loan_valid = False  # loan cerrado antes del trabajo
            except Exception:
                pass

        # Si el loan no era válido en esa fecha → no asociar loan
        if not loan_valid:
            loan_num  = ""
            portfolio = ""

        # Días abierto (para SM-only, desde primera orden SM)
        try:
            days = (datetime.now() - datetime.strptime(first_date, "%Y-%m-%d")).days if first_date and sm_open else 0
        except Exception:
            days = 0

        cases.append({
            "source":      "SM",
            "category":    category,
            "billed_to":   billed_to,
            "task_refno":  0,
            "subject":     "",
            "client":      client,
            "loan":        loan_num,
            "vin":         vin,
            "portfolio":   portfolio,
            "nls_open":    False,
            "status":      "OPEN" if sm_open else "CLOSED",
            "days_open":   days,
            "created":     first_date,
            "created_mon": mon,
            "completed":   "" if sm_open else last_date,
            "notes":       "",
            "sm_orders":   len(sm_orders_list),
            "sm_cost":     sm_cost,
            "sm_et":       sm_et,
            "sm_open":     sm_open,
            "sm_desc":     sm_desc[:120],
            "sm_first":    first_date,
            "sm_last":     last_date,
            "appt_note":   appt["note"][:120] if appt else "",
            "appt_status": appt["status"] if appt else "",
        })

    cases.sort(key=lambda c: c["created"], reverse=True)
    return cases

# ── Aggregations ──────────────────────────────────────────────────────────────
CATS = ["Insurance Claim (FYBA)", "Garantía", "Pérdida Total", "Repo / Reacondicionamiento", "Dealer (SuperCar)", "Externo"]
CAT_COLORS = {
    "Insurance Claim (FYBA)":    "#2E75B6",
    "Garantía":                  "#70AD47",
    "Pérdida Total":             "#ED7D31",
    "Repo / Reacondicionamiento":"#0070C0",
    "Dealer (SuperCar)":         "#7030A0",
    "Externo":                   "#808080",
}

def cat_stats(cases):
    stats = {c: {"total":0,"open":0,"closed":0,"cost":0.0,"et":0.0} for c in CATS}
    for c in cases:
        cat = c["category"]
        if cat not in stats:
            stats[cat] = {"total":0,"open":0,"closed":0,"cost":0.0,"et":0.0}
        stats[cat]["total"] += 1
        if c["nls_open"] or c["sm_open"]:
            stats[cat]["open"] += 1
        else:
            stats[cat]["closed"] += 1
        stats[cat]["cost"] += c["sm_cost"]
        stats[cat]["et"]   += c["sm_et"]
    return stats

def monthly_stats(cases):
    months = {}
    for c in cases:
        m = c["created_mon"]
        if not m:
            continue
        if m not in months:
            months[m] = {cat: {"new":0,"cost":0.0} for cat in CATS}
            months[m]["__total"] = {"new":0,"open":0,"cost":0.0}
        cat = c["category"]
        if cat not in months[m]:
            months[m][cat] = {"new":0,"cost":0.0}
        months[m][cat]["new"]  += 1
        months[m][cat]["cost"] += c["sm_cost"]
        months[m]["__total"]["new"]  += 1
        months[m]["__total"]["cost"] += c["sm_cost"]
        if c["nls_open"] or c["sm_open"]:
            months[m]["__total"]["open"] += 1
    return dict(sorted(months.items())[-14:])

# ── HTML helpers ──────────────────────────────────────────────────────────────
def fmt_usd(v):
    return f"${v:,.0f}"

def status_badge(case):
    is_open = case["nls_open"] or case["sm_open"]
    if is_open:
        days = case["days_open"]
        color = "danger" if days > 30 else "warning"
        return f'<span class="badge bg-{color}">Abierto {days}d</span>'
    return '<span class="badge bg-success">Cerrado</span>'

def cat_badge(cat):
    color_map = {
        "FYBA / Insurance":    "primary",
        "Garantía":            "success",
        "Pérdida Total":       "warning text-dark",
        "Shopmonkey (Externo)":"secondary",
    }
    cls = color_map.get(cat, "secondary")
    return f'<span class="badge bg-{cls}">{cat}</span>'

def appt_badge(st):
    m = {"Confirmed":"success","Declined":"danger","NoResponse":"warning text-dark"}
    cls = m.get(st,"secondary")
    return f'<span class="badge bg-{cls}">{st}</span>' if st else ""

# ── HTML generation ───────────────────────────────────────────────────────────
def build_html(cases):
    now_et     = datetime.now(ET)
    cur_month  = now_et.strftime("%Y-%m")
    prev_month = (now_et.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    cstats     = cat_stats(cases)
    monthly    = monthly_stats(cases)

    total_open   = sum(1 for c in cases if c["nls_open"] or c["sm_open"])
    total_closed = sum(1 for c in cases if not (c["nls_open"] or c["sm_open"]))
    total_cost   = sum(c["sm_cost"] for c in cases)
    open_cost    = sum(c["sm_cost"] for c in cases if c["nls_open"] or c["sm_open"])
    this_m_new   = sum(1 for c in cases if c["created_mon"] == cur_month)
    this_m_cost  = sum(c["sm_cost"] for c in cases if c["created_mon"] == cur_month)

    # ── Chart data ────────────────────────────────────────────────────────────
    months_sorted = sorted(monthly.keys())[-12:]
    chart_labels  = json.dumps(months_sorted)
    chart_ins  = json.dumps([monthly.get(m,{}).get("Insurance Claim (FYBA)",{}).get("new",0) for m in months_sorted])
    chart_guar = json.dumps([monthly.get(m,{}).get("Garantía",{}).get("new",0) for m in months_sorted])
    chart_vl   = json.dumps([monthly.get(m,{}).get("Pérdida Total",{}).get("new",0) for m in months_sorted])
    chart_repo = json.dumps([monthly.get(m,{}).get("Repo / Reacondicionamiento",{}).get("new",0) for m in months_sorted])
    chart_sc   = json.dumps([monthly.get(m,{}).get("Dealer (SuperCar)",{}).get("new",0) for m in months_sorted])
    chart_ext  = json.dumps([monthly.get(m,{}).get("Externo",{}).get("new",0) for m in months_sorted])
    chart_cost    = json.dumps([round(monthly.get(m,{}).get("__total",{}).get("cost",0),0) for m in months_sorted])

    pie_labels = json.dumps(["Insurance", "Garantía", "V.Loss", "Repo Recon", "SuperCar", "Externo"])
    pie_vals   = json.dumps([cstats.get(c,{}).get("total",0) for c in CATS])

    # ── Open cases table ──────────────────────────────────────────────────────
    open_cases = [c for c in cases if c["nls_open"] or c["sm_open"]]
    open_rows  = ""
    for c in sorted(open_cases, key=lambda x: x["days_open"], reverse=True)[:100]:
        open_rows += f"""
        <tr>
          <td>{cat_badge(c['category'])}</td>
          <td>{c['created']}</td>
          <td><strong>{c['client']}</strong></td>
          <td><code class="small">{c['vin']}</code></td>
          <td>{c['loan']}</td>
          <td><small>{c['portfolio']}</small></td>
          <td>{status_badge(c)}</td>
          <td class="text-end fw-bold">{fmt_usd(c['sm_cost'])}</td>
          <td><small>{c['billed_to']}</small></td>
          <td><small class="text-muted">{(c['appt_note'] or c['sm_desc'])[:80]}</small></td>
        </tr>"""

    # ── FYBA cases table ──────────────────────────────────────────────────────
    def cases_table(cat, limit=200):
        rows = ""
        for c in [x for x in cases if x["category"] == cat][:limit]:
            rows += f"""
            <tr>
              <td>{c['created']}</td>
              <td><strong>{c['client']}</strong></td>
              <td><code class="small">{c['vin']}</code></td>
              <td>{c['loan']}</td>
              <td><small>{c['portfolio']}</small></td>
              <td>{status_badge(c)}</td>
              <td>{c['completed'] or '—'}</td>
              <td class="text-end fw-bold">{fmt_usd(c['sm_cost'])}</td>
              <td>{appt_badge(c['appt_status'])}</td>
              <td><small class="text-muted">{(c['appt_note'] or c['sm_desc'] or c['notes'])[:80]}</small></td>
            </tr>"""
        return rows or '<tr><td colspan="10" class="text-center text-muted">Sin datos</td></tr>'

    rows_ins  = cases_table("Insurance Claim (FYBA)")
    rows_guar = cases_table("Garantía")
    rows_vl   = cases_table("Pérdida Total")
    rows_repo = cases_table("Repo / Reacondicionamiento")
    rows_sc   = cases_table("Dealer (SuperCar)")
    rows_ext  = cases_table("Externo")

    # ── Monthly summary table ─────────────────────────────────────────────────
    month_rows = ""
    for m in reversed(months_sorted):
        md = monthly.get(m, {})
        tot = md.get("__total", {})
        month_rows += f"""
        <tr>
          <td class="fw-semibold">{m}</td>
          <td class="text-end">{tot.get('new',0)}</td>
          <td class="text-end text-primary">{md.get('FYBA / Insurance',{}).get('new',0)}</td>
          <td class="text-end text-success">{md.get('Garantía',{}).get('new',0)}</td>
          <td class="text-end text-warning">{md.get('Pérdida Total',{}).get('new',0)}</td>
          <td class="text-end text-secondary">{md.get('Shopmonkey (Externo)',{}).get('new',0)}</td>
          <td class="text-end fw-bold">{fmt_usd(tot.get('cost',0))}</td>
          <td class="text-end text-danger">{tot.get('open',0)}</td>
        </tr>"""

    # ── KPI cards per category ────────────────────────────────────────────────
    def kpi_cat(cat, cls):
        s = cstats.get(cat, {})
        label = cat.replace("Shopmonkey (Externo)","Externo")
        return f"""
        <div class="col-6 col-md-3">
          <div class="card p-3 h-100 border-{cls}" style="border-left:4px solid!important">
            <div class="kpi-label">{label}</div>
            <div class="kpi-val" style="color:var(--bs-{cls})">{s.get('open',0)} <small class="text-muted fs-6">abiertos</small></div>
            <small class="text-muted">{s.get('total',0)} total &bull; {fmt_usd(s.get('cost',0))}</small>
          </div>
        </div>"""

    cat_kpi_html = (kpi_cat("Insurance Claim (FYBA)","primary") +
                    kpi_cat("Garantía","success") +
                    kpi_cat("Pérdida Total","warning") +
                    kpi_cat("Repo / Reacondicionamiento","info") +
                    kpi_cat("Dealer (SuperCar)","secondary") +
                    kpi_cat("Externo","dark"))

    mnames = {"01":"Ene","02":"Feb","03":"Mar","04":"Abr","05":"May","06":"Jun",
              "07":"Jul","08":"Ago","09":"Sep","10":"Oct","11":"Nov","12":"Dic"}
    cur_month_label = mnames.get(cur_month[5:],"") + " " + cur_month[:4]

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="1800">
  <title>Claims Dashboard — Brio Management</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <style>
    body{{background:#f0f4f8;font-family:'Segoe UI',Arial,sans-serif;}}
    .topbar{{background:linear-gradient(135deg,#1F3864,#2E75B6);color:#fff;padding:16px 28px 14px;}}
    .topbar h1{{font-size:1.4rem;font-weight:700;margin:0;letter-spacing:.3px;}}
    .topbar small{{opacity:.8;font-size:.82rem;}}
    .card{{border:none;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.07);}}
    .kpi-val{{font-size:1.9rem;font-weight:700;line-height:1.1;}}
    .kpi-label{{font-size:.78rem;text-transform:uppercase;letter-spacing:.5px;color:#6c757d;}}
    .section-title{{font-size:.85rem;font-weight:600;text-transform:uppercase;
                    letter-spacing:.6px;color:#1F3864;border-left:3px solid #2E75B6;
                    padding-left:8px;margin-bottom:12px;}}
    .chart-wrap{{position:relative;height:260px;}}
    .chart-wrap-sm{{position:relative;height:180px;}}
    .table th{{font-size:.75rem;text-transform:uppercase;letter-spacing:.4px;}}
    .table td{{font-size:.82rem;vertical-align:middle;}}
    .divider{{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;
              color:#fff;background:#1F3864;padding:4px 12px;border-radius:4px;margin:18px 0 10px;display:inline-block;}}
    .nav-tabs .nav-link{{font-size:.82rem;font-weight:600;}}
    code{{background:#eef;border-radius:3px;padding:1px 4px;font-size:.8em;}}
  </style>
</head>
<body>

<div class="topbar d-flex justify-content-between align-items-center flex-wrap gap-2">
  <div>
    <h1>Brio Management &mdash; Claims &amp; Garantías Dashboard</h1>
    <small>Actualizado: {now_et.strftime('%d/%m/%Y %H:%M')} ET &nbsp;&bull;&nbsp; NLS Tasks + Shopmonkey &nbsp;&bull;&nbsp; Auto-refresh 30min</small>
  </div>
  <div class="d-flex gap-2 flex-wrap">
    <span class="badge bg-danger fs-6 px-3 py-2">{total_open} Abiertos</span>
    <span class="badge bg-success fs-6 px-3 py-2">{total_closed} Cerrados</span>
    <span class="badge bg-warning text-dark fs-6 px-3 py-2">{fmt_usd(total_cost)} Shopmonkey</span>
  </div>
</div>

<div class="container-fluid px-4 py-3">

  <!-- KPI Overview -->
  <div class="row g-3 mb-3">
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">Total Casos</div>
        <div class="kpi-val text-dark">{len(cases)}</div>
        <small class="text-muted">Todos los tipos</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100 border border-danger">
        <div class="kpi-label">Casos Abiertos</div>
        <div class="kpi-val text-danger">{total_open}</div>
        <small class="text-muted">{fmt_usd(open_cost)} en curso</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">Total Shopmonkey $</div>
        <div class="kpi-val text-primary">{fmt_usd(total_cost)}</div>
        <small class="text-muted">Acumulado</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">{cur_month_label}</div>
        <div class="kpi-val text-success">{this_m_new}</div>
        <small class="text-muted">{fmt_usd(this_m_cost)} costo</small>
      </div>
    </div>
    {cat_kpi_html}
  </div>

  <!-- Charts row -->
  <div class="row g-3 mb-3">
    <div class="col-md-8">
      <div class="card p-3 h-100">
        <div class="section-title">Casos Nuevos por Mes &amp; Costo Shopmonkey</div>
        <div class="chart-wrap">
          <canvas id="chartMonthly"></canvas>
        </div>
      </div>
    </div>
    <div class="col-md-4">
      <div class="card p-3 h-100">
        <div class="section-title">Distribución por Categoría</div>
        <div class="chart-wrap">
          <canvas id="chartPie"></canvas>
        </div>
      </div>
    </div>
  </div>

  <!-- Tabs: Open + by category -->
  <div class="card p-3 mb-3">
    <ul class="nav nav-tabs mb-3" id="mainTabs">
      <li class="nav-item"><a class="nav-link active" data-bs-toggle="tab" href="#tabOpen">
        🔴 Abiertos <span class="badge bg-danger ms-1">{total_open}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabIns">
        🔵 Insurance FYBA <span class="badge bg-primary ms-1">{cstats.get('Insurance Claim (FYBA)',{}).get('total',0)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabGarantia">
        🟢 Garantía <span class="badge bg-success ms-1">{cstats.get('Garantía',{}).get('total',0)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabVL">
        🟠 Pérdida Total <span class="badge bg-warning text-dark ms-1">{cstats.get('Pérdida Total',{}).get('total',0)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabRepo">
        🔷 Repo Reacon. <span class="badge bg-info ms-1">{cstats.get('Repo / Reacondicionamiento',{}).get('total',0)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabSC">
        🟣 SuperCar <span class="badge bg-secondary ms-1">{cstats.get('Dealer (SuperCar)',{}).get('total',0)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabExt">
        ⚪ Externo <span class="badge bg-dark ms-1">{cstats.get('Externo',{}).get('total',0)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabMonthly">
        📊 Por Mes</a></li>
    </ul>

    <div class="tab-content">

      <!-- OPEN CASES -->
      <div class="tab-pane fade show active" id="tabOpen">
        <div class="section-title">Casos Abiertos — {total_open} casos &mdash; {fmt_usd(open_cost)} en curso</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr><th>Tipo</th><th>Fecha</th><th>Cliente</th><th>VIN</th><th>Loan</th>
                  <th>Portfolio</th><th>Estado</th><th class="text-end">Costo SM</th>
                  <th>Facturado a</th><th>Descripción</th></tr>
            </thead>
            <tbody>{open_rows or '<tr><td colspan="10" class="text-center text-success fw-bold">✓ No hay casos abiertos</td></tr>'}</tbody>
          </table>
        </div>
      </div>

      <!-- INSURANCE -->
      <div class="tab-pane fade" id="tabIns">
        <div class="section-title">Insurance Claims (FYBA) — Cliente Brio tuvo accidente · Facturado a FYBA Reinsurance</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr><th>Abierto</th><th>Cliente</th><th>VIN</th><th>Loan</th><th>Portfolio</th>
                  <th>Estado</th><th>Cerrado</th><th class="text-end">Costo SM</th><th>Cita</th><th>Nota</th></tr>
            </thead>
            <tbody>{rows_ins}</tbody>
          </table>
        </div>
      </div>

      <!-- GARANTIA -->
      <div class="tab-pane fade" id="tabGarantia">
        <div class="section-title">Garantía — Cliente Brio con problema mecánico · Facturado a Line Capital / DCJ</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr><th>Abierto</th><th>Cliente</th><th>VIN</th><th>Loan</th><th>Portfolio</th>
                  <th>Estado</th><th>Cerrado</th><th class="text-end">Costo SM</th><th>Cita</th><th>Nota</th></tr>
            </thead>
            <tbody>{rows_guar}</tbody>
          </table>
        </div>
      </div>

      <!-- VEHICLE LOSS -->
      <div class="tab-pane fade" id="tabVL">
        <div class="section-title">Pérdida Total / Vehicle Loss</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr><th>Abierto</th><th>Cliente</th><th>VIN</th><th>Loan</th><th>Portfolio</th>
                  <th>Estado</th><th>Cerrado</th><th class="text-end">Costo SM</th><th>Cita</th><th>Nota</th></tr>
            </thead>
            <tbody>{rows_vl}</tbody>
          </table>
        </div>
      </div>

      <!-- REPO -->
      <div class="tab-pane fade" id="tabRepo">
        <div class="section-title">Repo / Reacondicionamiento — Cliente no recuperó el auto · Valver lo acondiciona para reventa</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr><th>Fecha</th><th>VIN</th><th>Loan</th><th>Portfolio</th>
                  <th>Estado</th><th>Cerrado</th><th class="text-end">Costo SM</th><th>Facturado a</th><th>Descripción</th></tr>
            </thead>
            <tbody>{rows_repo}</tbody>
          </table>
        </div>
      </div>

      <!-- SUPERCAR -->
      <div class="tab-pane fade" id="tabSC">
        <div class="section-title">Dealer (SuperCar) — SuperCar manda su inventario a preparar · Facturado a Super Car Miami Group LLC</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr><th>Fecha</th><th>Cliente</th><th>VIN</th><th>Loan</th><th>Portfolio</th>
                  <th>Estado</th><th>Cerrado</th><th class="text-end">Costo SM</th><th>Cita</th><th>Descripción</th></tr>
            </thead>
            <tbody>{rows_sc}</tbody>
          </table>
        </div>
      </div>

      <!-- EXTERNO -->
      <div class="tab-pane fade" id="tabExt">
        <div class="section-title">Externo — Sin relación con Brio / sin cita en calendario</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr><th>Fecha</th><th>Cliente</th><th>VIN</th><th>Loan</th><th>Portfolio</th>
                  <th>Estado</th><th>Cerrado</th><th class="text-end">Costo SM</th><th>Cita</th><th>Nota</th></tr>
            </thead>
            <tbody>{rows_ext}</tbody>
          </table>
        </div>
      </div>

      <!-- MONTHLY -->
      <div class="tab-pane fade" id="tabMonthly">
        <div class="section-title">Resumen Mensual por Categoría</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr><th>Mes</th><th class="text-end">Total</th>
                  <th class="text-end text-primary">FYBA</th>
                  <th class="text-end text-success">Garantía</th>
                  <th class="text-end text-warning">V.Loss</th>
                  <th class="text-end text-secondary">Externo</th>
                  <th class="text-end">Costo SM</th>
                  <th class="text-end text-danger">Abiertos</th></tr>
            </thead>
            <tbody>{month_rows}</tbody>
          </table>
        </div>
      </div>

    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
const labels  = {chart_labels};
const ins   = {chart_ins};
const guar  = {chart_guar};
const vl    = {chart_vl};
const repo  = {chart_repo};
const sc    = {chart_sc};
const ext   = {chart_ext};
const costArr = {chart_cost};
const pieLabels = {pie_labels};
const pieVals   = {pie_vals};

new Chart(document.getElementById('chartMonthly'),{{
  type:'bar',
  data:{{
    labels,
    datasets:[
      {{label:'Insurance FYBA',  data:ins,  backgroundColor:'rgba(46,117,182,.8)'}},
      {{label:'Garantía',        data:guar, backgroundColor:'rgba(112,173,71,.8)'}},
      {{label:'V.Loss',          data:vl,   backgroundColor:'rgba(237,125,49,.8)'}},
      {{label:'Repo Reacon.',    data:repo, backgroundColor:'rgba(0,112,192,.6)'}},
      {{label:'SuperCar',        data:sc,   backgroundColor:'rgba(112,48,160,.6)'}},
      {{label:'Externo',         data:ext,  backgroundColor:'rgba(128,128,128,.4)'}},
    ]
  }},
  options:{{
    responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{position:'top'}}}},
    scales:{{x:{{stacked:true}},y:{{stacked:true,beginAtZero:true}}}}
  }}
}});

new Chart(document.getElementById('chartPie'),{{
  type:'doughnut',
  data:{{labels:pieLabels,datasets:[{{data:pieVals,
    backgroundColor:['#2E75B6','#70AD47','#ED7D31','#0070C0','#7030A0','#808080']}}]}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{position:'bottom',labels:{{font:{{size:10}}}}}}}}}}
}});
</script>
</body>
</html>"""
    return html

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    nls_tasks  = fetch_nls_tasks()
    repo_vins  = fetch_nls_repo_vins()
    orders     = fetch_orders()
    appts      = fetch_appointments()

    all_vids   = list(set(
        (o.get("vehicleId") or "") for o in orders + appts if o.get("vehicleId")
    ))
    vid_to_vin  = fetch_vins(all_vids)
    services    = fetch_services([o["id"] for o in orders])

    # VINs de Shopmonkey sin task NLS → buscar loan activo por fecha
    task_vins   = set(t["vin"] for t in nls_tasks if t["vin"])
    sm_vins     = set(v for v in vid_to_vin.values() if v not in task_vins)
    vin_history = fetch_nls_vin_history(sm_vins)

    cases = build_cases(nls_tasks, orders, appts, vid_to_vin, services, repo_vins, vin_history)

    stats = cat_stats(cases)
    print(f"\nCases built: {len(cases)}")
    for cat, s in stats.items():
        print(f"  {cat}: {s['total']} total | {s['open']} open | {fmt_usd(s['cost'])}")

    html = build_html(cases)

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nSaved: {out}")

if __name__ == "__main__":
    main()
