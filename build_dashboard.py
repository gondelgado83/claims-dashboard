"""
Claims & Citas Dashboard — Brio Management
Genera docs/index.html con datos de Shopmonkey + NLS.
Corre localmente via Task Scheduler cada hora.
"""

import os, json, time, pyodbc
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request, urllib.parse

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
SM_HDR  = {"Authorization": f"Bearer {SM_TOKEN}", "Content-Type": "application/json"}

ET = timezone(timedelta(hours=-4))  # Eastern (EDT)

ENG_KW = ['engine','motor','transmis','long block','short block','transaxle','cvt','rebuilt']

# ── HTTP helper ───────────────────────────────────────────────────────────────
def sm_get(path, retries=4):
    url = SM_BASE + path
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=SM_HDR)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                return None

# ── Fetch orders (smart pagination) ──────────────────────────────────────────
def fetch_orders():
    print("Fetching orders...")
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
        print(f"  offset={offset} new={len(new)} total={len(orders)}")
        if len(new) == 0:
            empty_streak += 1
            if empty_streak >= 6:
                break
        else:
            empty_streak = 0
        offset += 100
    print(f"Orders: {len(orders)}")
    return orders

# ── Fetch appointments ────────────────────────────────────────────────────────
def fetch_appointments():
    print("Fetching appointments...")
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
    print(f"Appointments: {len(appts)}")
    return appts

# ── Fetch VINs ────────────────────────────────────────────────────────────────
def fetch_vins(vehicle_ids):
    print(f"Fetching VINs for {len(vehicle_ids)} vehicles...")
    vid_to_vin = {}
    done = [0]

    def get_vin(vid):
        data = sm_get(f"/vehicle/{vid}")
        if data:
            v = data.get("data", {})
            vin = v.get("vin") or ""
            if vin and len(vin) >= 6:
                return vid, vin.upper().strip()
        return vid, None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(get_vin, vid): vid for vid in vehicle_ids}
        for fut in as_completed(futs):
            vid, vin = fut.result()
            if vin:
                vid_to_vin[vid] = vin
            done[0] += 1
            if done[0] % 50 == 0:
                print(f"  VINs: {done[0]}/{len(vehicle_ids)}")
    print(f"VINs fetched: {len(vid_to_vin)}")
    return vid_to_vin

# ── Fetch services ────────────────────────────────────────────────────────────
def fetch_services(order_ids):
    print(f"Fetching services for {len(order_ids)} orders...")
    result = {}
    done = [0]

    def get_svc(oid):
        data = sm_get(f"/order/{oid}/service")
        if not data:
            return oid, []
        svcs = []
        for s in data.get("data", []):
            name = s.get("name", "")
            cost = s.get("totalCostCents", 0) or 0
            labors = s.get("labors", []) or []
            parts  = s.get("parts", []) or []
            labor_desc = "; ".join(
                f"{lb.get('name','')}" for lb in labors if lb.get("name")
            )
            part_desc = "; ".join(
                f"{pt.get('name','')}" for pt in parts if pt.get("name")
            )
            desc = labor_desc or part_desc or name
            is_et = any(k in (name + desc).lower() for k in ENG_KW)
            svcs.append({"name": name, "desc": desc, "cost": cost / 100, "is_et": is_et})
        return oid, svcs

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(get_svc, oid): oid for oid in order_ids}
        for fut in as_completed(futs):
            oid, svcs = fut.result()
            result[oid] = svcs
            done[0] += 1
            if done[0] % 100 == 0:
                print(f"  Services: {done[0]}/{len(order_ids)}")
    print("Services fetched")
    return result

# ── NLS lookup ────────────────────────────────────────────────────────────────
def nls_lookup(vins):
    if not vins:
        return {}
    print(f"NLS lookup for {len(vins)} VINs...")
    conn = pyodbc.connect(NLS_CONN)
    placeholders = ",".join(["?"] * len(vins))
    sql = f"""
        SELECT cv.vin,
               CAST(l.loan_number AS VARCHAR(20)) AS loan_number,
               l.name,
               COALESCE(lpc.portfolio_description,'') AS portfolio,
               l.status_code_no
        FROM collateral_vehicle cv
        JOIN loanacct_collateral_link lcl ON lcl.collateral_id = cv.collateral_id
        JOIN loanacct l ON l.acctrefno = lcl.acctrefno
        LEFT JOIN loan_port_codes lpc ON lpc.portfolio_code_id = l.portfolio_code_id
        WHERE cv.vin IN ({placeholders})
    """
    cur = conn.cursor()
    cur.execute(sql, list(vins))
    nls = {}
    for row in cur.fetchall():
        vin = row[0].upper().strip()
        if vin not in nls:
            nls[vin] = {"loan": row[1], "name": row[2], "portfolio": row[3], "status": row[4]}
    conn.close()
    print(f"NLS: {len(nls)} VINs matched")
    return nls

# ── Build order records ───────────────────────────────────────────────────────
def build_order_records(orders, vid_to_vin, services_map, nls_map):
    records = []
    for o in orders:
        vid      = o.get("vehicleId") or ""
        vin      = vid_to_vin.get(vid, "")
        nls      = nls_map.get(vin, {})
        svcs     = services_map.get(o["id"], [])
        total    = (o.get("totalCostCents") or 0) / 100
        if total == 0 and svcs:
            total = sum(s["cost"] for s in svcs)
        et_cost  = sum(s["cost"] for s in svcs if s["is_et"])
        is_et    = any(s["is_et"] for s in svcs)
        svc_desc = " | ".join(s["name"] for s in svcs[:4] if s["name"])
        order_type = o.get("orderType", o.get("type", ""))
        is_open = order_type in ("RepairOrder", "Estimate")
        raw_date = o.get("createdDate") or o.get("date") or ""
        try:
            dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).astimezone(ET)
            date_str  = dt.strftime("%Y-%m-%d")
            month_key = dt.strftime("%Y-%m")
        except Exception:
            date_str  = raw_date[:10] if raw_date else ""
            month_key = raw_date[:7] if raw_date else ""
        records.append({
            "id":        o["id"],
            "number":    o.get("number", ""),
            "date":      date_str,
            "month":     month_key,
            "vin":       vin,
            "loan":      nls.get("loan", ""),
            "client":    nls.get("name", o.get("coalescedName", "")),
            "portfolio": nls.get("portfolio", ""),
            "status":    nls.get("status", ""),
            "total":     total,
            "et_cost":   et_cost,
            "is_et":     is_et,
            "is_open":   is_open,
            "order_type": order_type,
            "desc":      svc_desc,
        })
    records.sort(key=lambda r: r["date"], reverse=True)
    return records

# ── Build appointment records ─────────────────────────────────────────────────
def build_appt_records(appts, vid_to_vin, nls_map):
    seen_client = {}
    for a in appts:
        vid   = a.get("vehicleId") or ""
        vin   = vid_to_vin.get(vid, "")
        nls   = nls_map.get(vin, {})
        raw_date = a.get("date") or a.get("scheduledDate") or ""
        try:
            dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).astimezone(ET)
            date_str  = dt.strftime("%Y-%m-%d")
            month_key = dt.strftime("%Y-%m")
        except Exception:
            date_str  = raw_date[:10] if raw_date else ""
            month_key = raw_date[:7] if raw_date else ""
        conf_status = a.get("confirmationStatus", "NoResponse")
        rec = {
            "date":      date_str,
            "month":     month_key,
            "name":      a.get("name", ""),
            "vin":       vin,
            "loan":      nls.get("loan", ""),
            "portfolio": nls.get("portfolio", ""),
            "note":      a.get("note", "") or "",
            "status":    conf_status,
        }
        client_key = a.get("name", "").strip().upper()
        if client_key not in seen_client:
            seen_client[client_key] = rec
        else:
            existing = seen_client[client_key]
            priority = {"Confirmed": 0, "NoResponse": 1, "Declined": 2}
            if priority.get(conf_status, 9) < priority.get(existing["status"], 9):
                seen_client[client_key] = rec
    records = sorted(seen_client.values(), key=lambda r: r["date"], reverse=True)
    return records

# ── Monthly aggregates ────────────────────────────────────────────────────────
def monthly_stats(records, appt_records):
    months_o = {}
    for r in records:
        m = r["month"]
        if not m:
            continue
        if m not in months_o:
            months_o[m] = {"orders": 0, "total": 0.0, "et_cost": 0.0, "open": 0}
        months_o[m]["orders"] += 1
        months_o[m]["total"]  += r["total"]
        months_o[m]["et_cost"] += r["et_cost"]
        if r["is_open"]:
            months_o[m]["open"] += 1

    months_a = {}
    for a in appt_records:
        m = a["month"]
        if not m:
            continue
        if m not in months_a:
            months_a[m] = {"total": 0, "confirmed": 0, "declined": 0, "noresp": 0}
        months_a[m]["total"] += 1
        if a["status"] == "Confirmed":
            months_a[m]["confirmed"] += 1
        elif a["status"] == "Declined":
            months_a[m]["declined"] += 1
        else:
            months_a[m]["noresp"] += 1

    all_months = sorted(set(list(months_o.keys()) + list(months_a.keys())))[-14:]
    stats = []
    for m in all_months:
        o = months_o.get(m, {"orders":0,"total":0,"et_cost":0,"open":0})
        a = months_a.get(m, {"total":0,"confirmed":0,"declined":0,"noresp":0})
        stats.append({"month": m, **o,
                      "appts": a["total"], "conf": a["confirmed"],
                      "decl": a["declined"], "noresp": a["noresp"]})
    return stats

# ── Portfolio stats ───────────────────────────────────────────────────────────
def portfolio_stats(records):
    ports = {}
    for r in records:
        p = r["portfolio"] or "Sin Portfolio"
        if p not in ports:
            ports[p] = {"orders": 0, "total": 0.0, "et_cost": 0.0, "open": 0}
        ports[p]["orders"] += 1
        ports[p]["total"]  += r["total"]
        ports[p]["et_cost"] += r["et_cost"]
        if r["is_open"]:
            ports[p]["open"] += 1
    return sorted(ports.items(), key=lambda x: x[1]["total"], reverse=True)

# ── HTML generation ───────────────────────────────────────────────────────────
def fmt_usd(v):
    return f"${v:,.0f}"

def badge_status(st):
    m = {"Confirmed": ("success","Confirmed ✓"),
         "Declined":  ("danger","Declined ✗"),
         "NoResponse":("warning","Sin respuesta")}
    cls, label = m.get(st, ("secondary", st))
    return f'<span class="badge bg-{cls}">{label}</span>'

def build_html(order_records, appt_records, monthly, portfolio):
    now_et = datetime.now(ET)
    cur_month = now_et.strftime("%Y-%m")
    prev_month = (now_et.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    # ── KPI calculations ──────────────────────────────────────────────────────
    total_orders = len(order_records)
    total_cost   = sum(r["total"] for r in order_records)
    total_et     = sum(r["et_cost"] for r in order_records)
    open_orders  = [r for r in order_records if r["is_open"]]
    open_cost    = sum(r["total"] for r in open_orders)

    this_month_r = [r for r in order_records if r["month"] == cur_month]
    last_month_r = [r for r in order_records if r["month"] == prev_month]
    this_m_cost  = sum(r["total"] for r in this_month_r)
    last_m_cost  = sum(r["total"] for r in last_month_r)
    this_m_et    = sum(r["et_cost"] for r in this_month_r)

    total_appts  = len(appt_records)
    conf_appts   = sum(1 for a in appt_records if a["status"] == "Confirmed")
    decl_appts   = sum(1 for a in appt_records if a["status"] == "Declined")
    noresp_appts = sum(1 for a in appt_records if a["status"] == "NoResponse")
    this_m_appts = sum(1 for a in appt_records if a["month"] == cur_month)
    conf_this_m  = sum(1 for a in appt_records if a["month"] == cur_month and a["status"] == "Confirmed")

    et_pct = (total_et / total_cost * 100) if total_cost else 0

    # ── Chart data ────────────────────────────────────────────────────────────
    last12 = monthly[-12:]
    chart_labels  = json.dumps([m["month"] for m in last12])
    chart_total   = json.dumps([round(m["total"],2) for m in last12])
    chart_et      = json.dumps([round(m["et_cost"],2) for m in last12])
    chart_appts   = json.dumps([m["appts"] for m in last12])
    chart_conf    = json.dumps([m["conf"] for m in last12])

    port_labels = json.dumps([p for p,_ in portfolio[:8]])
    port_vals   = json.dumps([round(v["total"],2) for _,v in portfolio[:8]])

    # ── Open orders rows ──────────────────────────────────────────────────────
    open_rows_html = ""
    for r in sorted(open_orders, key=lambda x: x["date"], reverse=True)[:50]:
        et_badge = '<span class="badge bg-warning text-dark">Motor/Trans</span>' if r["is_et"] else ""
        open_rows_html += f"""
        <tr>
          <td>{r['date']}</td>
          <td><small>{r['number']}</small></td>
          <td>{r['client']}</td>
          <td><code class="text-muted small">{r['vin']}</code></td>
          <td>{r['loan']}</td>
          <td><small>{r['portfolio']}</small></td>
          <td class="text-end fw-bold">{fmt_usd(r['total'])}</td>
          <td>{et_badge}</td>
          <td><small class="text-muted">{r['desc'][:80]}</small></td>
        </tr>"""

    # ── Appointments rows (last 60 days) ──────────────────────────────────────
    cutoff = (now_et - timedelta(days=60)).strftime("%Y-%m-%d")
    recent_appts = [a for a in appt_records if a["date"] >= cutoff]
    appt_rows_html = ""
    for a in recent_appts[:80]:
        row_cls = "table-success" if a["status"]=="Confirmed" else (
                  "table-danger" if a["status"]=="Declined" else "table-warning")
        appt_rows_html += f"""
        <tr class="{row_cls}">
          <td>{a['date']}</td>
          <td>{a['name']}</td>
          <td><code class="text-muted small">{a['vin']}</code></td>
          <td>{a['loan']}</td>
          <td><small>{a['portfolio']}</small></td>
          <td>{badge_status(a['status'])}</td>
          <td><small>{a['note'][:100]}</small></td>
        </tr>"""

    # ── Monthly summary rows ──────────────────────────────────────────────────
    monthly_rows_html = ""
    for m in reversed(monthly[-14:]):
        trend = "↑" if m["total"] > (monthly[monthly.index(m)-1]["total"] if monthly.index(m)>0 else 0) else "↓"
        monthly_rows_html += f"""
        <tr>
          <td class="fw-semibold">{m['month']}</td>
          <td class="text-end">{m['orders']}</td>
          <td class="text-end fw-bold">{fmt_usd(m['total'])}</td>
          <td class="text-end text-warning">{fmt_usd(m['et_cost'])}</td>
          <td class="text-end">{m['open']}</td>
          <td class="text-end">{m['appts']}</td>
          <td class="text-end text-success">{m['conf']}</td>
          <td class="text-end text-danger">{m['decl']}</td>
        </tr>"""

    # ── Portfolio rows ────────────────────────────────────────────────────────
    port_rows_html = ""
    for p, v in portfolio:
        pct = (v["et_cost"] / v["total"] * 100) if v["total"] else 0
        port_rows_html += f"""
        <tr>
          <td>{p}</td>
          <td class="text-end">{v['orders']}</td>
          <td class="text-end fw-bold">{fmt_usd(v['total'])}</td>
          <td class="text-end text-warning">{fmt_usd(v['et_cost'])}</td>
          <td class="text-end"><small>{pct:.0f}%</small></td>
          <td class="text-end text-danger">{v['open']}</td>
        </tr>"""

    # ── Month label helper ────────────────────────────────────────────────────
    mnames = {"01":"Ene","02":"Feb","03":"Mar","04":"Abr","05":"May","06":"Jun",
              "07":"Jul","08":"Ago","09":"Sep","10":"Oct","11":"Nov","12":"Dic"}
    cur_month_label = mnames.get(cur_month[5:], cur_month[5:]) + " " + cur_month[:4]

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="1800">
  <title>Claims & Citas — Brio Management</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <style>
    body {{ background:#f0f4f8; font-family:'Segoe UI',Arial,sans-serif; }}
    .topbar {{ background:linear-gradient(135deg,#1F3864,#2E75B6); color:#fff; padding:16px 28px 14px; }}
    .topbar h1 {{ font-size:1.4rem; font-weight:700; margin:0; letter-spacing:.3px; }}
    .topbar small {{ opacity:.8; font-size:.82rem; }}
    .card {{ border:none; border-radius:12px; box-shadow:0 2px 8px rgba(0,0,0,.07); }}
    .kpi-val {{ font-size:2rem; font-weight:700; color:#1F3864; line-height:1.1; }}
    .kpi-val-sm {{ font-size:1.5rem; font-weight:700; color:#1F3864; line-height:1.1; }}
    .kpi-label {{ font-size:.78rem; text-transform:uppercase; letter-spacing:.5px; color:#6c757d; }}
    .section-title {{ font-size:.85rem; font-weight:600; text-transform:uppercase;
                      letter-spacing:.6px; color:#1F3864; border-left:3px solid #2E75B6;
                      padding-left:8px; margin-bottom:12px; }}
    .chart-wrap {{ position:relative; height:260px; }}
    .chart-wrap-sm {{ position:relative; height:200px; }}
    .table th {{ font-size:.78rem; text-transform:uppercase; letter-spacing:.4px; }}
    .table td {{ font-size:.83rem; vertical-align:middle; }}
    .divider {{ font-size:.7rem; font-weight:700; text-transform:uppercase; letter-spacing:1px;
                color:#fff; background:#1F3864; padding:4px 12px; border-radius:4px; margin:18px 0 10px; }}
    code {{ background:#eef; border-radius:3px; padding:1px 4px; }}
    .nav-tabs .nav-link {{ font-size:.82rem; font-weight:600; }}
  </style>
</head>
<body>

<div class="topbar d-flex justify-content-between align-items-center flex-wrap gap-2">
  <div>
    <h1>Brio Management &mdash; Claims &amp; Citas Dashboard</h1>
    <small>Actualizado: {now_et.strftime('%d/%m/%Y %H:%M')} ET &nbsp;&bull;&nbsp; Datos: Shopmonkey + NLS &nbsp;&bull;&nbsp; Se refresca automáticamente</small>
  </div>
  <div class="d-flex gap-2 flex-wrap">
    <span class="badge bg-light text-dark fs-6 px-3 py-2">{total_orders} Órdenes</span>
    <span class="badge bg-danger fs-6 px-3 py-2">{len(open_orders)} Abiertos</span>
    <span class="badge bg-warning text-dark fs-6 px-3 py-2">{fmt_usd(total_cost)} Total</span>
  </div>
</div>

<div class="container-fluid px-4 py-3">

  <!-- ── SECCIÓN CLAIMS ── -->
  <div class="divider">Claims / Órdenes de Trabajo</div>

  <!-- KPI Claims -->
  <div class="row g-3 mb-3">
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">Total Órdenes</div>
        <div class="kpi-val">{total_orders}</div>
        <small class="text-muted">Todas</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">Total Facturado</div>
        <div class="kpi-val">{fmt_usd(total_cost)}</div>
        <small class="text-muted">Acumulado</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">Motor / Trans</div>
        <div class="kpi-val text-warning">{fmt_usd(total_et)}</div>
        <small class="text-muted">{et_pct:.0f}% del total</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100 border-danger" style="border:2px solid #dc3545!important">
        <div class="kpi-label">Claims Abiertos</div>
        <div class="kpi-val text-danger">{len(open_orders)}</div>
        <small class="text-muted">{fmt_usd(open_cost)} pendiente</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">{cur_month_label}</div>
        <div class="kpi-val text-primary">{fmt_usd(this_m_cost)}</div>
        <small class="text-muted">{len(this_month_r)} órdenes</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">Motor/Trans Este Mes</div>
        <div class="kpi-val-sm text-warning">{fmt_usd(this_m_et)}</div>
        <small class="text-muted">Mes anterior: {fmt_usd(last_m_cost)}</small>
      </div>
    </div>
  </div>

  <!-- Charts Claims -->
  <div class="row g-3 mb-3">
    <div class="col-md-8">
      <div class="card p-3 h-100">
        <div class="section-title">Costo por Mes (últimos 12 meses)</div>
        <div class="chart-wrap">
          <canvas id="chartMonthly"></canvas>
        </div>
      </div>
    </div>
    <div class="col-md-4">
      <div class="card p-3 h-100">
        <div class="section-title">Por Portfolio</div>
        <div class="chart-wrap">
          <canvas id="chartPortfolio"></canvas>
        </div>
      </div>
    </div>
  </div>

  <!-- ── SECCIÓN CITAS ── -->
  <div class="divider">Citas / Appointments</div>

  <!-- KPI Citas -->
  <div class="row g-3 mb-3">
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">Total Citas</div>
        <div class="kpi-val">{total_appts}</div>
        <small class="text-muted">Todas</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">Confirmadas</div>
        <div class="kpi-val text-success">{conf_appts}</div>
        <small class="text-muted">{conf_appts/total_appts*100:.0f}% del total</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">Declinadas</div>
        <div class="kpi-val text-danger">{decl_appts}</div>
        <small class="text-muted">&nbsp;</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">Sin Respuesta</div>
        <div class="kpi-val text-warning">{noresp_appts}</div>
        <small class="text-muted">&nbsp;</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="kpi-label">Citas {cur_month_label}</div>
        <div class="kpi-val text-primary">{this_m_appts}</div>
        <small class="text-muted">{conf_this_m} confirmadas</small>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card p-3 h-100">
        <div class="section-title mt-0 mb-2">Citas por Mes</div>
        <div class="chart-wrap-sm">
          <canvas id="chartAppts"></canvas>
        </div>
      </div>
    </div>
  </div>

  <!-- ── TABLAS con tabs ── -->
  <div class="card p-3 mb-3">
    <ul class="nav nav-tabs mb-3" id="mainTabs">
      <li class="nav-item"><a class="nav-link active" data-bs-toggle="tab" href="#tabOpen">
        Claims Abiertos <span class="badge bg-danger ms-1">{len(open_orders)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabCitas">
        Citas Recientes <span class="badge bg-secondary ms-1">{len(recent_appts)}</span></a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabMonthly">Resumen Mensual</a></li>
      <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabPortfolio">Por Portfolio</a></li>
    </ul>

    <div class="tab-content">

      <!-- Claims Abiertos -->
      <div class="tab-pane fade show active" id="tabOpen">
        <div class="section-title">Claims Abiertos ({len(open_orders)} órdenes &mdash; {fmt_usd(open_cost)} pendiente)</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr>
                <th>Fecha</th><th>#</th><th>Cliente</th><th>VIN</th><th>Loan</th>
                <th>Portfolio</th><th class="text-end">Monto</th><th>Tipo</th><th>Descripción</th>
              </tr>
            </thead>
            <tbody>{open_rows_html or '<tr><td colspan="9" class="text-center text-muted">No hay claims abiertos</td></tr>'}</tbody>
          </table>
        </div>
      </div>

      <!-- Citas Recientes -->
      <div class="tab-pane fade" id="tabCitas">
        <div class="section-title">Citas últimos 60 días ({len(recent_appts)} citas)</div>
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr>
                <th>Fecha</th><th>Cliente</th><th>VIN</th><th>Loan</th>
                <th>Portfolio</th><th>Status</th><th>Caso / Nota</th>
              </tr>
            </thead>
            <tbody>{appt_rows_html or '<tr><td colspan="7" class="text-center text-muted">Sin citas recientes</td></tr>'}</tbody>
          </table>
        </div>
      </div>

      <!-- Resumen Mensual -->
      <div class="tab-pane fade" id="tabMonthly">
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr>
                <th>Mes</th>
                <th class="text-end">Órdenes</th>
                <th class="text-end">Total $</th>
                <th class="text-end">Motor/Trans $</th>
                <th class="text-end">Abiertos</th>
                <th class="text-end">Citas</th>
                <th class="text-end">Conf.</th>
                <th class="text-end">Decl.</th>
              </tr>
            </thead>
            <tbody>{monthly_rows_html}</tbody>
          </table>
        </div>
      </div>

      <!-- Por Portfolio -->
      <div class="tab-pane fade" id="tabPortfolio">
        <div class="table-responsive">
          <table class="table table-hover table-sm">
            <thead class="table-dark">
              <tr>
                <th>Portfolio</th>
                <th class="text-end">Órdenes</th>
                <th class="text-end">Total $</th>
                <th class="text-end">Motor/Trans $</th>
                <th class="text-end">ET %</th>
                <th class="text-end">Abiertos</th>
              </tr>
            </thead>
            <tbody>{port_rows_html}</tbody>
          </table>
        </div>
      </div>

    </div><!-- tab-content -->
  </div>

</div><!-- container -->

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
const labels = {chart_labels};
const totals = {chart_total};
const etCost = {chart_et};
const apptTotals = {chart_appts};
const apptConf  = {chart_conf};
const portLabels = {port_labels};
const portVals   = {port_vals};

// Monthly cost chart
new Chart(document.getElementById('chartMonthly'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [
      {{ label: 'Total Facturado', data: totals,
         backgroundColor: 'rgba(46,117,182,0.7)', borderColor: '#2E75B6', borderWidth:1 }},
      {{ label: 'Motor/Trans', data: etCost,
         backgroundColor: 'rgba(255,192,0,0.7)', borderColor: '#FFC000', borderWidth:1 }},
    ]
  }},
  options: {{
    responsive:true, maintainAspectRatio:false,
    plugins:{{ legend:{{ position:'top' }}, tooltip:{{ callbacks:{{ label: ctx => '$'+ctx.parsed.y.toLocaleString() }} }} }},
    scales:{{ y:{{ ticks:{{ callback: v => '$'+v.toLocaleString() }} }} }}
  }}
}});

// Portfolio pie
new Chart(document.getElementById('chartPortfolio'), {{
  type: 'doughnut',
  data: {{ labels: portLabels, datasets:[{{ data: portVals,
    backgroundColor:['#2E75B6','#ED7D31','#A9D18E','#FFC000','#FF0000','#7030A0','#00B0F0','#92D050'] }}] }},
  options: {{ responsive:true, maintainAspectRatio:false,
    plugins:{{ legend:{{ position:'bottom', labels:{{ font:{{size:10}} }} }} }} }}
}});

// Appointments bar
new Chart(document.getElementById('chartAppts'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [
      {{ label: 'Confirmadas', data: apptConf,
         backgroundColor: 'rgba(25,135,84,0.7)' }},
      {{ label: 'Total Citas', data: apptTotals,
         backgroundColor: 'rgba(108,117,125,0.3)' }},
    ]
  }},
  options: {{
    responsive:true, maintainAspectRatio:false,
    plugins:{{ legend:{{ position:'bottom', labels:{{ font:{{size:9}} }} }} }},
    scales:{{ y:{{ beginAtZero:true }} }}
  }}
}});
</script>
</body>
</html>"""
    return html

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    orders     = fetch_orders()
    appts      = fetch_appointments()

    all_vids   = list(set(
        (o.get("vehicleId") or "") for o in orders + appts
        if o.get("vehicleId")
    ))
    vid_to_vin = fetch_vins(all_vids)

    services   = fetch_services([o["id"] for o in orders])

    all_vins   = set(vid_to_vin.values())
    nls_map    = nls_lookup(all_vins)

    order_records = build_order_records(orders, vid_to_vin, services, nls_map)
    appt_records  = build_appt_records(appts, vid_to_vin, nls_map)
    monthly       = monthly_stats(order_records, appt_records)
    portfolio     = portfolio_stats(order_records)

    html = build_html(order_records, appt_records, monthly, portfolio)

    out_dir = os.path.join(os.path.dirname(__file__), "docs")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved: {out}")
    print(f"Orders: {len(order_records)} | Open: {sum(1 for r in order_records if r['is_open'])} | Appts: {len(appt_records)}")

if __name__ == "__main__":
    main()
