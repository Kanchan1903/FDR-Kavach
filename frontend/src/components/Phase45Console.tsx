"use client";

import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${api}${path}`, { credentials: "include" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail ?? `Request failed (${response.status})`);
  return data as T;
}
async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const csrf = await get<{ csrfToken: string }>("/api/csrf/");
  const response = await fetch(`${api}${path}`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf.csrfToken }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail ?? `Request failed (${response.status})`);
  return data as T;
}

type Institution = { id: string; code: string; name: string };
type Fdr = { id: string; fdr_number: string; maturity_date: string };
type Coverage = { date: string; required: string; submitted: string; bank_confirmed: string; shortfall: string; risk: boolean };
type Release = { id: string; fdr_id: string; fdr_number: string; status: string; requested_by: string; reviewer: string | null; approver: string | null; checklist: Record<string, { passed: boolean }>; review_note: string; discharge_evidence: string };
type Dashboard = {
  [key: string]: number | string | null | Array<{ rule: string; priority: number }> | Array<{ priority: number; type: string; id?: string; title: string; reason: string }> | Record<string, number> | undefined;
  verification_turnaround_avg_hours?: number | null;
  coverage_shortfall_amount?: string;
  priority_queue?: Array<{ priority: number; type: string; id?: string; title: string; reason: string }>;
  case_severity?: Record<string, number>;
};

export default function Phase45Console({ role, username, view = "all" }: { role: string; username?: string; view?: "all" | "dashboard" | "coverage" | "release" | "history" | "impact" }) {
  const [institutions, setInstitutions] = useState<Institution[]>([]);
  const [records, setRecords] = useState<Fdr[]>([]);
  const [releaseFdr, setReleaseFdr] = useState("");
  const [institutionId, setInstitutionId] = useState("");
  const [coverage, setCoverage] = useState<Coverage[]>([]);
  const [releases, setReleases] = useState<Release[]>([]);
  const [dashboard, setDashboard] = useState<Dashboard>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dischargeEvidence, setDischargeEvidence] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [historyFdr, setHistoryFdr] = useState("");
  const [validOn, setValidOn] = useState("");
  const [knownOn, setKnownOn] = useState("");
  const [historyRows, setHistoryRows] = useState<Array<{ version: number; snapshot: Record<string, unknown>; valid_from: string; valid_to: string | null; known_at: string }>>([]);

  async function refresh() {
    try {
      const [inst, fdr, stats, releaseRows] = await Promise.all([
        get<Institution[]>("/api/institutions/"), get<Fdr[]>("/api/fdrs/"), get<Dashboard>("/api/dashboard/"), get<Release[]>("/api/release-requests/"),
      ]);
      setInstitutions(inst); setRecords(fdr); setDashboard(stats); setReleases(releaseRows);
      if (!institutionId && inst.length) setInstitutionId(String(inst[0].id));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load oversight data."); }
    finally { setLoading(false); }
  }
  async function loadCoverage() {
    if (!institutionId) return;
    try {
      const result = await get<{ days: Coverage[] }>(`/api/coverage/timeline/?institution_id=${institutionId}&start=${new Date().toISOString().slice(0, 10)}&end=${new Date(Date.now() + 29 * 86400000).toISOString().slice(0, 10)}`);
      setCoverage(result.days);
    } catch (e) { setError(e instanceof Error ? e.message : "Coverage calculation failed."); }
  }
  useEffect(() => { void refresh(); }, []);
  useEffect(() => { void loadCoverage(); }, [institutionId]);
  useEffect(() => {
    if (!records.some(record => record.id === releaseFdr)) setReleaseFdr(records[0]?.id ?? "");
  }, [records, releaseFdr]);

  async function releaseAction(id: string, action: string, extra: Record<string, unknown> = {}) {
    const confirmation = action === "approve"
      ? "Approve this release? This action records your approval and cannot be withdrawn from this screen."
      : action === "discharge"
        ? "Record this bank discharge evidence and move the FDR to released status?"
        : action === "close"
          ? "Close this release request?"
          : null;
    if (confirmation && !window.confirm(confirmation)) return;
    setBusy(true); setError(""); setNotice("");
    try { await post(`/api/release-requests/${id}/action/`, { action, ...extra }); await refresh(); setNotice(`Release request ${id}: ${action} recorded.`); }
    catch (e) { setError(e instanceof Error ? e.message : "Release action failed."); }
    finally { setBusy(false); }
  }
  async function requestRelease(fdrId: string) {
    setBusy(true); setError("");
    try {
      const csrf = await get<{ csrfToken: string }>("/api/csrf/");
      const response = await fetch(`${api}/api/release-requests/`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf.csrfToken }, body: JSON.stringify({ fdr_id: fdrId }) });
      const body = await response.json(); if (!response.ok) throw new Error(body.detail ?? "Request failed");
      await refresh(); setNotice(`Release request created for ${body.fdr_number}.`);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not request release."); }
    finally { setBusy(false); }
  }
  async function download(path: string, filename: string) {
    try { const response = await fetch(`${api}${path}`, { credentials: "include" }); if (!response.ok) throw new Error("Export failed."); const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url); }
    catch (e) { setError(e instanceof Error ? e.message : "Export failed."); }
  }
  async function loadHistory() {
    if (!historyFdr) return;
    const query = new URLSearchParams();
    if (validOn) query.set("valid_on", new Date(validOn).toISOString());
    if (knownOn) query.set("known_on", new Date(knownOn).toISOString());
    try {
      const rows = await get<Array<{ version: number; snapshot: Record<string, unknown>; valid_from: string; valid_to: string | null; known_at: string }>>(`/api/fdrs/${historyFdr}/history/?${query.toString()}`);
      setHistoryRows(rows);
    } catch (e) { setError(e instanceof Error ? e.message : "History lookup failed."); }
  }
  const metrics = ["active", "expiring_30_days", "matured", "renewed", "released", "release_recorded", "pending", "pending_release", "open_cases", "sla_breaches", "pending_verification", "coverage_gaps", "outstanding_risks"];
  const canRequest = ["INSTITUTION_USER", "ADMIN", "SENIOR_OFFICER"].includes(role);
  const canExport = ["REVIEWER", "APPROVER", "SENIOR_OFFICER", "ADMIN", "AUDITOR"].includes(role);
  const canReview = ["REVIEWER", "SENIOR_OFFICER", "ADMIN"].includes(role);
  const canApprove = ["APPROVER", "SENIOR_OFFICER", "ADMIN"].includes(role);
  const canDischarge = ["SENIOR_OFFICER", "ADMIN"].includes(role);
  const showDashboard = view === "all" || view === "dashboard" || view === "impact";
  const showCoverage = view === "all" || view === "coverage";
  const showRelease = view === "all" || view === "release";
  const showHistory = view === "all" || view === "history";
  const dashboardTiles = [
    ["Active FDRs", "active", "teal"], ["Expiring in 30 days", "expiring_30_days", "amber"],
    ["Open cases", "open_cases", "red"], ["Coverage gaps", "coverage_gaps", "blue"],
  ] as const;
  const severityRows = Object.entries(dashboard.case_severity ?? {}).map(([name, value]) => ({ name, value: Number(value) })).filter(row => row.value > 0);
  const severityColors: Record<string, string> = { HIGH: "#dc4b48", MEDIUM: "#e9a12b", LOW: "#15956f", INFORMATIONAL: "#8a9aab" };
  return <div className="mt-5 grid gap-5">
    {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}{notice && <p role="status" className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</p>}{loading && <p role="status" className="animate-pulse rounded-lg bg-white p-4 text-sm text-slate-600">Loading portfolio and coverage data…</p>}
    {showDashboard && <>
      <Card><CardContent className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3"><div><h3 className="font-bold text-slate-900">Portfolio overview</h3><p className="mt-1 text-sm text-slate-500">Live record counts and work requiring attention.</p></div>{canExport && <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => void download("/api/reports/export/?export_format=csv", "fdr-report.csv")}>Export CSV</Button><Button size="sm" variant="outline" onClick={() => void download("/api/reports/export/?export_format=pdf", "fdr-report.pdf")}>Export PDF</Button></div>}</div>
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">{dashboardTiles.map(([label, key, tone]) => <div key={key} className={`dashboard-kpi kpi-${tone}`}><span>{label}</span><strong>{(dashboard[key] as number) ?? 0}</strong><small>{key === "active" ? "Current active portfolio" : key === "expiring_30_days" ? "Requires timely review" : key === "open_cases" ? "Open and assigned" : "Risk under active rules"}</small></div>)}</div>
        <p className="text-xs text-slate-500">Average verification turnaround: {dashboard.verification_turnaround_avg_hours ?? "-"} hours <span className="mx-2 text-slate-300">|</span> Total coverage shortfall: INR {Number(dashboard.coverage_shortfall_amount ?? 0).toLocaleString("en-IN")}</p>
      </CardContent></Card>
      <div className="grid gap-5 xl:grid-cols-[1.7fr_1fr]">
        <Card><CardContent><div className="mb-4"><h3 className="font-bold text-slate-900">Priority work</h3><p className="mt-1 text-sm text-slate-500">Items ranked by the server using role and SLA information.</p></div>
          {dashboard.priority_queue?.length ? <div className="overflow-x-auto"><table className="portal-table min-w-[620px]"><thead><tr><th>Item</th><th>Issue</th><th>Priority</th></tr></thead><tbody>{dashboard.priority_queue.slice(0, 8).map((item, index) => <tr key={`${item.type}-${item.id ?? item.title}-${index}`}><td className="font-semibold text-slate-800">{item.title}</td><td className="text-slate-600">{item.reason}</td><td><span className={`priority-tag ${item.priority <= 2 ? "priority-high" : "priority-normal"}`}>P{item.priority}</span></td></tr>)}</tbody></table></div> : <div className="portal-empty"><p>No priority items are currently assigned to your role.</p></div>}
        </CardContent></Card>
        <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-1">
          <Card><CardContent><div><h3 className="font-bold text-slate-900">Open cases by severity</h3><p className="mt-1 text-sm text-slate-500">Current case workload.</p></div>{severityRows.length ? <div className="severity-chart"><ResponsiveContainer width="48%" height={166}><PieChart><Pie data={severityRows} dataKey="value" nameKey="name" innerRadius={48} outerRadius={72} paddingAngle={2}>{severityRows.map(row => <Cell key={row.name} fill={severityColors[row.name.toUpperCase()] ?? "#1f497d"}/>)}</Pie><Tooltip/></PieChart></ResponsiveContainer><ul className="severity-legend">{severityRows.map(row => <li key={row.name}><span style={{ backgroundColor: severityColors[row.name.toUpperCase()] ?? "#1f497d" }}/><span>{row.name.replaceAll("_", " ")}</span><b>{row.value}</b></li>)}</ul></div> : <div className="portal-empty mt-4">No open cases are reported.</div>}</CardContent></Card>
          <Card><CardContent><div><h3 className="font-bold text-slate-900">Portfolio status</h3><p className="mt-1 text-sm text-slate-500">FDR lifecycle counts from current records.</p></div><div className="mt-3 h-36">{metrics.length ? <ResponsiveContainer width="100%" height="100%"><BarChart data={[["Active", "active"], ["Matured", "matured"], ["Renewed", "renewed"], ["Released", "released"], ["Pending", "pending"]].map(([name, key]) => ({ name, count: Number(dashboard[key] ?? 0) }))} layout="vertical" margin={{ left: 8, right: 12 }}><CartesianGrid stroke="#e7edf3" horizontal={false}/><XAxis type="number" allowDecimals={false} hide/><YAxis type="category" dataKey="name" width={66} tick={{ fontSize: 11, fill: "#53677f" }} axisLine={false} tickLine={false}/><Tooltip/><Bar dataKey="count" fill="#1f497d" radius={[0, 5, 5, 0]} barSize={13}/></BarChart></ResponsiveContainer> : null}</div></CardContent></Card>
        </div>
      </div>
    </>}
    {showCoverage && <Card><CardContent><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-bold">Daily coverage timeline</h3><p className="text-sm text-slate-500">Risk days show uncovered required amounts under the active rule set.</p></div><select value={institutionId} onChange={e => setInstitutionId(e.target.value)} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm">{institutions.map(row => <option key={row.id} value={row.id}>{row.code} · {row.name}</option>)}</select></div><div className="mt-4 h-72 w-full">{coverage.length ? <ResponsiveContainer width="100%" height="100%"><BarChart data={coverage}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="date" tick={{ fontSize: 10 }}/><YAxis/><Tooltip/><Bar dataKey="required" fill="#94a3b8" name="Required"/><Bar dataKey="bank_confirmed" fill="#0f766e" name="Bank confirmed"/><Bar dataKey="shortfall" fill="#dc2626" name="Shortfall"/></BarChart></ResponsiveContainer> : <p className="pt-10 text-sm text-slate-500">Select an institution and configure an active Rule Set to calculate coverage.</p>}</div><div className="flex flex-wrap gap-2">{coverage.filter(x => x.risk).slice(0, 8).map(day => <span key={day.date} className="rounded-full bg-red-100 px-3 py-1 text-xs font-semibold text-red-800">Risk {day.date}: shortfall {Number(day.shortfall).toLocaleString("en-IN")}</span>)}</div></CardContent></Card>}
    {showRelease && <Card className="release-readiness-card"><CardContent className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><h3 className="text-base font-bold text-slate-900">Release readiness</h3><p className="mt-1 text-sm text-slate-500">Independent review, approval, and discharge tracking.</p></div>
        {canRequest && <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="release-fdr">Select an FDR for release</label>
          <select id="release-fdr" value={releaseFdr} onChange={event => setReleaseFdr(event.target.value)} disabled={!records.length} className="min-w-56 rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-800 shadow-sm focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20">
            {records.length ? records.map(row => <option key={row.id} value={row.id}>{row.fdr_number}</option>) : <option value="">No accessible FDR records</option>}
          </select>
          <Button size="sm" disabled={busy || !releaseFdr} onClick={() => void requestRelease(releaseFdr)}>Request release</Button>
        </div>}
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[["Requested", "REQUESTED"], ["Under scrutiny", "UNDER_SCRUTINY"], ["Approved", "APPROVED_FOR_RELEASE"], ["Discharged / closed", "RELEASE_RECORDED"]].map(([label, status]) => <div key={status} className="rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2"><p className="text-[11px] font-medium text-slate-500">{label}</p><p className="mt-0.5 text-lg font-bold text-ink">{releases.filter(row => status === "RELEASE_RECORDED" ? ["RELEASE_RECORDED", "CLOSED"].includes(row.status) : row.status === status).length}</p></div>)}
      </div>
      {releases.length ? <div className="space-y-3">{releases.map(row => <article key={row.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="font-bold text-slate-900">{row.fdr_number}</p><p className="mt-1 text-xs text-slate-500">Requested by {row.requested_by} | Reviewer {row.reviewer ?? "pending"} | Approver {row.approver ?? "pending"}</p></div><span className="rounded-full border border-blue-100 bg-blue-50 px-3 py-1 text-xs font-bold text-ink">{row.status.replaceAll("_", " ")}</span></div>
        {Object.keys(row.checklist).length > 0 && <div className="mt-3 flex flex-wrap gap-2">{Object.entries(row.checklist).map(([name, item]) => <span key={name} className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${item.passed ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"}`}>{name.replaceAll("_", " ")}: {item.passed ? "Ready" : "Pending"}</span>)}</div>}
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
          {canReview && row.status === "REQUESTED" && row.requested_by !== username && <Button size="sm" disabled={busy} onClick={() => void releaseAction(row.id, "review", { note: "Scrutiny completed." })}>Begin scrutiny</Button>}
          {canApprove && row.status === "UNDER_SCRUTINY" && row.requested_by !== username && row.reviewer !== username && <Button size="sm" disabled={busy} onClick={() => void releaseAction(row.id, "approve")}>Approve release</Button>}
          {row.status === "REQUESTED" && canReview && row.requested_by === username && <p className="text-xs text-slate-500">The requester cannot review their own request.</p>}
          {row.status === "UNDER_SCRUTINY" && canApprove && (row.requested_by === username || row.reviewer === username) && <p className="text-xs text-slate-500">Approval requires a different requester and reviewer.</p>}
          {canDischarge && row.status === "APPROVED_FOR_RELEASE" && <><label className="sr-only" htmlFor={`discharge-${row.id}`}>Discharge evidence reference for {row.fdr_number}</label><input id={`discharge-${row.id}`} value={dischargeEvidence[row.id] ?? ""} onChange={event => setDischargeEvidence(current => ({ ...current, [row.id]: event.target.value }))} placeholder="Discharge evidence reference" className="min-w-56 rounded-lg border border-slate-300 px-3 py-2 text-sm"/><Button size="sm" disabled={busy || (dischargeEvidence[row.id] ?? "").trim().length < 10} onClick={() => void releaseAction(row.id, "discharge", { evidence: dischargeEvidence[row.id] })}>Record discharge</Button></>}
          {canDischarge && row.status === "RELEASE_RECORDED" && <Button size="sm" disabled={busy} onClick={() => void releaseAction(row.id, "close")}>Close request</Button>}
          {!canReview && !canApprove && !canDischarge && <p className="text-xs text-slate-500">Release decisions are handled by authorized reviewers and approvers.</p>}
        </div>
      </article>)}</div> : <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center"><p className="text-sm font-semibold text-slate-700">No release requests yet</p><p className="mt-1 text-xs text-slate-500">{canRequest ? "Select an FDR above to begin its release workflow." : "Requests will appear here when an institution submits one."}</p></div>}
    </CardContent></Card>}
    {showHistory && <Card><CardContent><h3 className="font-bold">Bitemporal record history</h3><p className="mt-1 text-sm text-slate-500">View the version valid on one date, optionally constrained by what officials knew at another date.</p><div className="mt-3 flex flex-wrap items-end gap-3"><select value={historyFdr} onChange={e => setHistoryFdr(e.target.value)} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"><option value="">Select FDR</option>{records.map(row => <option key={row.id} value={row.id}>{row.fdr_number}</option>)}</select><label className="text-xs">Valid on<input type="datetime-local" value={validOn} onChange={e => setValidOn(e.target.value)} className="mt-1 block rounded-md border border-slate-300 px-2 py-2 text-sm"/></label><label className="text-xs">Known on<input type="datetime-local" value={knownOn} onChange={e => setKnownOn(e.target.value)} className="mt-1 block rounded-md border border-slate-300 px-2 py-2 text-sm"/></label><Button size="sm" variant="outline" disabled={!historyFdr} onClick={() => void loadHistory()}>View history</Button></div>{historyRows.length > 0 && <ol className="mt-3 space-y-2">{historyRows.map(row => <li key={row.version} className="rounded-lg bg-slate-50 p-3 text-xs"><strong>Version {row.version}</strong> · Valid {new Date(row.valid_from).toLocaleString()} – {row.valid_to ? new Date(row.valid_to).toLocaleString() : "current"} · Known {new Date(row.known_at).toLocaleString()}<pre className="mt-2 overflow-auto">{JSON.stringify(row.snapshot, null, 2)}</pre></li>)}</ol>}</CardContent></Card>}
    {canDischarge && <LegacyImport refresh={refresh} setError={setError} setNotice={setNotice}/>}
  </div>;
}

function LegacyImport({ refresh, setError, setNotice }: { refresh: () => Promise<void>; setError: (value: string) => void; setNotice: (value: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<{ rows: number; valid: number; errors: Array<{ row: number; errors: Record<string, string[]> }> } | null>(null);
  const [dryRun, setDryRun] = useState(true);
  async function submit() {
    if (!file) return;
    try { const csrf = await get<{ csrfToken: string }>("/api/csrf/"); const data = new FormData(); data.append("file", file); data.append("dry_run", String(dryRun)); const response = await fetch(`${api}/api/imports/legacy/`, { method: "POST", credentials: "include", headers: { "X-CSRFToken": csrf.csrfToken }, body: data }); const result = await response.json(); if (!response.ok) throw new Error(result.detail ?? "Import failed"); setReport(result); setNotice(dryRun ? "Dry-run validation completed; no records were written." : "Validated legacy records imported."); if (!dryRun) await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "Legacy import failed."); }
  }
  return <Card><CardContent><h3 className="font-bold">Legacy CSV/XLSX import</h3><p className="mt-1 text-sm text-slate-500">Validate first; any invalid row blocks a committed import.</p><div className="mt-3 flex flex-wrap items-center gap-3"><input type="file" accept=".csv,.xlsx" onChange={e => setFile(e.target.files?.[0] ?? null)} className="text-sm"/><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={dryRun} onChange={e => setDryRun(e.target.checked)}/>Dry run</label><Button size="sm" disabled={!file} onClick={() => void submit()}>{dryRun ? "Validate import" : "Import validated rows"}</Button></div>{report && <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm">{report.valid} valid of {report.rows} row(s){report.errors.length > 0 && <ul className="mt-2 list-disc pl-5 text-red-700">{report.errors.map(row => <li key={row.row}>Row {row.row}: {JSON.stringify(row.errors)}</li>)}</ul>}</div>}</CardContent></Card>;
}





