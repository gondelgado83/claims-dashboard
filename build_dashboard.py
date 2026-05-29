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
def sm_get(path, retries=4):
    url = SM_BASE + path
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=SM_HDR, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
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
        category = {2: "FYBA / Insurance", 4: "Garantía", 5: "Pérdida Total"}.get(int(tmpl_no), "Otro")
        billed_to = {2: "FYBA Reinsurance", 4: "Brio (Garantía)", 5: "Seguro / Total Loss"}.get(int(tmpl_no), "")
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

# ── NLS: fetch repossessed VINs (para detectar FYBA Remarketing) ──────────────
def fetch_nls_repo_vins():
    """VINs cuyo loan está en status repossessed (10) o repo in dealer (28)."""
    conn = pyodbc.connect(NLS_CONN)
    cur  = conn.cursor()
    cur.execute("""
        SELECT DISTINCT cv.vin
        FROM collateral_vehicle cv
        JOIN loanacct_collateral_link lcl ON lcl.collateral_id = cv.collateral_id
        JOIN loanacct l ON l.acctrefno = lcl.acctrefno
        WHERE l.status_code_no IN (10, 28)
           OR l.acctrefno IN (
               SELECT acctrefno FROM loanacct_statuses
               WHERE status_code_no IN (10, 28)
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
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(get_vin, vid): vid for vid in vehicle_ids}
        for fut in as_completed(futs):
            vid, vin = fut.result()
            if vin:
                vid_to_vin[vid] = vin
            done[0] += 1
            if done[0] % 100 == 0:
                print(f"  VINs: {done[0]}/{len(vehicle_ids)}")
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
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(get_svc, oid): oid for oid in order_ids}
        for fut in as_completed(futs):
            oid, svcs = fut.result()
            result[oid] = svcs
            done[0] += 1
            if done[0] % 100 == 0:
                print(f"  Services: {done[0]}/{len(order_ids)}")
    print("Services done")
    return result

# ── Build unified case records ────────────────────────────────────────────────
def is_fyba_sm(name):
    """True si el nombre de la cita/orden indica que FYBA es el cliente."""
    return "FYBA" in (name or "").upper()

def build_cases(nls_tasks, orders, appts, vid_to_vin, services_map, repo_vins):
    """
    Returns list of unified case dicts.
    Priority: NLS task = the case record.
    Shopmonkey orders/appts linked by VIN are attached as supplementary data.
    Orders with VINs that have NO NLS task → category "Shopmonkey (Externo)"
    """

    # Index Shopmonkey orders by VIN
    vin_to_orders = {}
    for o in orders:
        vid = o.get("vehicleId") or ""
        vin = vid_to_vin.get(vid, "")
        if not vin:
            vin = "__no_vin__" + o["id"]
        svcs     = services_map.get(o["id"], [])
        total    = (o.get("totalCostCents") or 0) / 100
        if total == 0:
            total = sum(s["cost"] for s in svcs)
        et_cost  = sum(s["cost"] for s in svcs if s["is_et"])
        is_et    = any(s["is_et"] for s in svcs)
        svc_desc = " | ".join(s["name"] for s in svcs[:4] if s["name"])
        order_type = o.get("orderType", o.get("type",""))
        is_open    = order_type in ("RepairOrder", "Estimate")
        date_str, mon = parse_date(o.get("createdDate") or o.get("date",""))
        sm_name  = o.get("coalescedName","")
        rec = {
            "sm_id": o["id"], "sm_number": o.get("number",""),
            "sm_name": sm_name, "date": date_str, "month": mon,
            "total": total, "et_cost": et_cost, "is_et": is_et,
            "is_open": is_open, "order_type": order_type, "desc": svc_desc,
        }
        if vin not in vin_to_orders:
            vin_to_orders[vin] = []
        vin_to_orders[vin].append(rec)

    # Index Shopmonkey appointments by VIN (deduplicated: prefer Confirmed)
    vin_to_appts = {}
    for a in appts:
        vid = a.get("vehicleId") or ""
        vin = vid_to_vin.get(vid, "")
        if not vin:
            continue
        date_str, mon = parse_date(a.get("date") or a.get("scheduledDate",""))
        conf = a.get("confirmationStatus","NoResponse")
        rec = {
            "name": a.get("name",""), "date": date_str, "month": mon,
            "note": a.get("note","") or "", "status": conf,
        }
        if vin not in vin_to_appts:
            vin_to_appts[vin] = rec
        else:
            prio = {"Confirmed":0,"NoResponse":1,"Declined":2}
            if prio.get(conf,9) < prio.get(vin_to_appts[vin]["status"],9):
                vin_to_appts[vin] = rec

    # VINs that have NLS tasks
    task_vins = set(t["vin"] for t in nls_tasks if t["vin"])

    # Build case list from NLS tasks
    cases = []
    for t in nls_tasks:
        vin = t["vin"]
        sm_orders = vin_to_orders.get(vin, [])
        sm_appt   = vin_to_appts.get(vin)
        sm_cost   = sum(o["total"] for o in sm_orders)
        sm_et     = sum(o["et_cost"] for o in sm_orders)
        sm_open   = any(o["is_open"] for o in sm_orders)
        sm_desc   = " | ".join(o["desc"] for o in sm_orders[:2] if o["desc"])
        sm_dates  = sorted(set(o["date"] for o in sm_orders if o["date"]))
        sm_first  = sm_dates[0] if sm_dates else ""
        sm_last   = sm_dates[-1] if sm_dates else ""
        appt_note = sm_appt["note"][:120] if sm_appt else ""
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

    # Shopmonkey records with NO NLS task (external / FYBA remarketing / other)
    for vin, sm_orders_list in vin_to_orders.items():
        if vin.startswith("__no_vin__") or vin in task_vins:
            continue
        sm_cost = sum(o["total"] for o in sm_orders_list)
        sm_et   = sum(o["et_cost"] for o in sm_orders_list)
        sm_open = any(o["is_open"] for o in sm_orders_list)
        sm_desc = " | ".join(o["desc"] for o in sm_orders_list[:2] if o["desc"])
        sm_dates = sorted(set(o["date"] for o in sm_orders_list if o["date"]))
        first_date = sm_dates[0] if sm_dates else ""
        last_date  = sm_dates[-1] if sm_dates else ""
        mon = first_date[:7] if first_date else ""
        client = sm_orders_list[0]["sm_name"] if sm_orders_list else ""
        appt   = vin_to_appts.get(vin)
        appt_name = appt["name"] if appt else ""

        # Detectar FYBA Remarketing: FYBA figura como cliente en SM Y el vehículo
        # está en status repo en NLS (o no tiene loan activo) → FYBA lo arregla para reventa
        fyba_in_sm = is_fyba_sm(appt_name) or is_fyba_sm(client)
        is_repo_vin = vin in repo_vins
        if fyba_in_sm:
            category  = "FYBA / Remarketing"
            billed_to = "FYBA Reinsurance (Remarketing)"
        else:
            category  = "Shopmonkey (Externo)"
            billed_to = "Cliente directo"

        cases.append({
            "source":      "SM",
            "category":    category,
            "billed_to":   billed_to,
            "task_refno":  0,
            "subject":     "",
            "client":      client,
            "loan":        "",
            "vin":         vin,
            "portfolio":   "",
            "nls_open":    False,
            "status":      "OPEN" if sm_open else "CLOSED",
            "days_open":   0,
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
CATS = ["FYBA / Insurance", "FYBA / Remarketing", "Garantía", "Pérdida Total", "Shopmonkey (Externo)"]
CAT_COLORS = {
    "FYBA / Insurance":    "#2E75B6",
    "FYBA / Remarketing":  "#0070C0",
    "Garantía":            "#70AD47",
    "Pérdida Total":       "#ED7D31",
    "Shopmonkey (Externo)":"#7030A0",
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
    chart_fyba    = json.dumps([monthly.get(m,{}).get("FYBA / Insurance",{}).get("new",0) for m in months_sorted])
    chart_fybar   = json.dumps([monthly.get(m,{}).get("FYBA / Remarketing",{}).get("new",0) for m in months_sorted])
    chart_guar    = json.dumps([monthly.get(m,{}).get("Garantía",{}).get("new",0) for m in months_sorted])
    chart_vl      = json.dumps([monthly.get(m,{}).get("Pérdida Total",{}).get("new",0) for m in months_sorted])
    chart_ext     = json.dumps([monthly.get(m,{}).get("Shopmonkey (Externo)",{}).get("new",0) for m in months_sorted])
    chart_cost    = json.dumps([round(monthly.get(m,{}).get("__total",{}).get("cost",0),0) for m in months_sorted])

    pie_labels = json.dumps(list(CATS))
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

    rows_fyba  = cases_table("FYBA / Insurance")
    rows_fybar = cases_table("FYBA / Remarketing")
    rows_guar  = cases_table("Garantía")
    rows_vl    = cases_table("Pérdida Total")
    rows_ext   = cases_table("Shopmonkey (Externo)")

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

    cat_kpi_html = (kpi_cat("FYBA / Insurance","primary") +
                    kpi_cat("FYBA / Remarketing","info") +
                    kpi_cat("Garantía","success") +
                    kpi_cat("Pérdida Total","warning") +
                    kpi_cat("Shopmonkey (Externo)","secondary"))

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
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabFYBA">
        🔵 FYBA Claim <span class="badge bg-primary ms-1">{cstats.get('FYBA / Insurance',{}).get('total',0)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabFYBAR">
        🔷 FYBA Remarketing <span class="badge bg-info ms-1">{cstats.get('FYBA / Remarketing',{}).get('total',0)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabGarantia">
        🟢 Garantía <span class="badge bg-success ms-1">{cstats.get('Garantía',{}).get('total',0)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabVL">
        🟠 Pérdida Total <span class="badge bg-warning text-dark ms-1">{cstats.get('Pérdida Total',{}).get('total',0)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabExt">
        ⚪ Externo Shopmonkey</a></li>
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

      <!-- FYBA -->
      <div class="tab-pane fade" id="tabFYBA">
        <div class="section-title">FYBA / Insurance Claims — Facturado a FYBA Reinsurance</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr><th>Abierto</th><th>Cliente</th><th>VIN</th><th>Loan</th><th>Portfolio</th>
                  <th>Estado</th><th>Cerrado</th><th class="text-end">Costo SM</th><th>Cita</th><th>Nota</th></tr>
            </thead>
            <tbody>{rows_fyba}</tbody>
          </table>
        </div>
      </div>

      <!-- FYBA REMARKETING -->
      <div class="tab-pane fade" id="tabFYBAR">
        <div class="section-title">FYBA Remarketing — Repo recuperado, FYBA paga arreglos para reventa</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr><th>Fecha</th><th>Cliente/VIN</th><th>VIN</th><th>Loan</th><th>Portfolio</th>
                  <th>Estado</th><th>Cerrado</th><th class="text-end">Costo SM</th><th>Cita</th><th>Descripción</th></tr>
            </thead>
            <tbody>{rows_fybar}</tbody>
          </table>
        </div>
      </div>

      <!-- GARANTIA -->
      <div class="tab-pane fade" id="tabGarantia">
        <div class="section-title">Garantía — Mecánica cubierta por préstamo Brio</div>
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

      <!-- EXTERNO -->
      <div class="tab-pane fade" id="tabExt">
        <div class="section-title">Shopmonkey (Externo) — Sin task en NLS</div>
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
const fyba    = {chart_fyba};
const fybar   = {chart_fybar};
const guar    = {chart_guar};
const vl      = {chart_vl};
const ext     = {chart_ext};
const costArr = {chart_cost};
const pieLabels = {pie_labels};
const pieVals   = {pie_vals};

new Chart(document.getElementById('chartMonthly'),{{
  type:'bar',
  data:{{
    labels,
    datasets:[
      {{label:'FYBA Claim',       data:fyba,  backgroundColor:'rgba(46,117,182,.7)'}},
      {{label:'FYBA Remarketing', data:fybar, backgroundColor:'rgba(0,112,192,.5)'}},
      {{label:'Garantía',         data:guar,  backgroundColor:'rgba(112,173,71,.7)'}},
      {{label:'V.Loss',           data:vl,    backgroundColor:'rgba(237,125,49,.7)'}},
      {{label:'Externo',          data:ext,   backgroundColor:'rgba(112,48,160,.4)'}},
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
    backgroundColor:['#2E75B6','#0070C0','#70AD47','#ED7D31','#7030A0']}}]}},
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
    vid_to_vin = fetch_vins(all_vids)
    services   = fetch_services([o["id"] for o in orders])

    cases = build_cases(nls_tasks, orders, appts, vid_to_vin, services, repo_vins)

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
