"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import Phase45Console from "@/components/Phase45Console";

const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const staffRoles = ["REVIEWER", "APPROVER", "SENIOR_OFFICER", "ADMIN", "AUDITOR"];
const canImport = (role: string) => ["SENIOR_OFFICER", "ADMIN"].includes(role);

type Institution = { id: string; code: string; name: string };
type Fdr = {
  id: string; fdr_number: string; bank_name: string; branch_name: string; principal_amount: string;
  issue_date: string; maturity_date: string; purpose: string; status: string;
  reverification_required: boolean; document_count: number;
};
type DocumentRow = { id: string; filename: string; size_bytes: number; sha256: string; uploaded_at: string; extracted_at: string | null };
type Extracted = { documentId: string; fields: Record<string, { value: string; confidence: number }> };
type EvidenceRow = { public_id: string; evidence_type: "DECLARED" | "DOCUMENT_OBSERVED" | "BANK_CONFIRMED"; field_name: string; value: unknown; confidence: string | null; corrected_by_user: boolean; recorded_at: string };
type FdrCase = {
  id: string; case_type: string; title: string; description: string; fdr_number: string | null;
  owner: { id: string; username: string } | null; severity: string; due_at: string; status: string;
  closure_evidence: string; escalation_count: number; can_update: boolean;
};
type StaffUser = { id: string; username: string; role: string };
type DuplicateFinding = { type: string; normalized_value?: string; sha256?: string; records: Array<{ id?: string; fdr_id?: string; document_id?: string; fdr_number: string; institution: string }> };
type BatchResult = { id: string; classification: string; fdr_number: string | null; bank_row_number: number | null; details: Record<string, unknown> };
type Alert = { id: string; case_id: string; kind: string; message: string; created_at: string; read: boolean };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${api}${path}`, { credentials: "include", ...init });
  const payload = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload ? String(payload.detail) : `Request failed (${response.status}).`;
    throw new Error(detail);
  }
  return payload as T;
}

async function csrf(): Promise<string> {
  const data = await request<{ csrfToken: string }>("/api/csrf/");
  return data.csrfToken;
}

async function sendJson<T>(path: string, method: "POST" | "PATCH", data: unknown): Promise<T> {
  const token = await csrf();
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRFToken": token },
    body: JSON.stringify(data),
  });
}

function formatBytes(size: number) {
  return size < 1024 * 1024 ? `${Math.ceil(size / 1024)} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export default function OperationsConsole({ role, initialTab = "records", presentation = "all" }: { role: string; initialTab?: "records" | "reconciliation" | "cases" | "oversight"; presentation?: "all" | "repository" | "passport" | "submission" }) {
  const [tab, setTab] = useState<"records" | "reconciliation" | "cases" | "oversight">(initialTab);
  const [institutions, setInstitutions] = useState<Institution[]>([]);
  const [records, setRecords] = useState<Fdr[]>([]);
  const [documents, setDocuments] = useState<Record<string, DocumentRow[]>>({});
  const [cases, setCases] = useState<FdrCase[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [staff, setStaff] = useState<StaffUser[]>([]);
  const [duplicates, setDuplicates] = useState<DuplicateFinding[]>([]);
  const [batchResults, setBatchResults] = useState<BatchResult[]>([]);
  const [selectedVersions, setSelectedVersions] = useState<Record<string, Array<{ version_number: number; change_reason: string; valid_from: string; snapshot: Record<string, unknown> }>>>({});
  const [passports, setPassports] = useState<Record<string, EvidenceRow[]>>({});
  const [extraction, setExtraction] = useState<Record<string, Extracted>>({});
  const [corrections, setCorrections] = useState<Record<string, Record<string, string>>>({});
  const [institutionId, setInstitutionId] = useState("");
  const [recordQuery, setRecordQuery] = useState("");
  const [recordStatus, setRecordStatus] = useState("ALL");
  const [fdrNumber, setFdrNumber] = useState("");
  const [bankName, setBankName] = useState("");
  const [branchName, setBranchName] = useState("");
  const [amount, setAmount] = useState("");
  const [issueDate, setIssueDate] = useState("");
  const [maturityDate, setMaturityDate] = useState("");
  const [purpose, setPurpose] = useState("");
  const [renewalOf, setRenewalOf] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const canCreate = ["INSTITUTION_USER", "ADMIN", "SENIOR_OFFICER"].includes(role);
  const canSeeReconciliation = staffRoles.includes(role);
  const canManageCases = ["ADMIN", "SENIOR_OFFICER"].includes(role);
  const openCases = useMemo(() => cases.filter(item => item.status !== "CLOSED"), [cases]);
  const visibleRecords = useMemo(() => records.filter(row => {
    const query = recordQuery.trim().toLowerCase();
    const matchesQuery = !query || [row.fdr_number, row.bank_name, row.branch_name].some(value => value.toLowerCase().includes(query));
    return matchesQuery && (recordStatus === "ALL" || row.status === recordStatus);
  }), [records, recordQuery, recordStatus]);

  async function loadDocuments(fdrId: string) {
    const data = await request<DocumentRow[]>(`/api/fdrs/${fdrId}/documents/`);
    setDocuments(current => ({ ...current, [fdrId]: data }));
  }

  async function refresh() {
    setError(null);
    try {
      const [institutionRows, fdrRows, caseRows, alertRows, duplicateRows] = await Promise.all([
        request<Institution[]>("/api/institutions/"), request<Fdr[]>("/api/fdrs/"),
        request<FdrCase[]>("/api/cases/"), request<Alert[]>("/api/case-alerts/"),
        request<DuplicateFinding[]>("/api/reconciliation/duplicates/"),
      ]);
      setInstitutions(institutionRows); setRecords(fdrRows); setCases(caseRows); setAlerts(alertRows); setDuplicates(duplicateRows);
      if (!institutionId && institutionRows.length) setInstitutionId(String(institutionRows[0].id));
      await Promise.all(fdrRows.map(row => loadDocuments(row.id).catch(() => undefined)));
      if (canSeeReconciliation) {
        const results = await request<BatchResult[]>("/api/reconciliation/results/");
        setBatchResults(results);
      }
      if (canManageCases) {
        const staffRows = await request<StaffUser[]>("/api/users/staff/");
        setStaff(staffRows);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load records.");
    } finally { setLoading(false); }
  }

  useEffect(() => { void refresh(); }, []);

  async function uploadDocument(fdrId: string, file: File) {
    const token = await csrf();
    const form = new FormData(); form.append("file", file);
    const uploaded = await request<{ id: string; sha256: string }>(`/api/fdrs/${fdrId}/documents/`, {
      method: "POST", headers: { "X-CSRFToken": token }, body: form,
    });
    const extracted = await sendJson<{ document_id: string; fields: Extracted["fields"] }>(
      `/api/fdrs/${fdrId}/documents/${uploaded.id}/extract/`, "POST", {},
    );
    setExtraction(current => ({ ...current, [fdrId]: { documentId: uploaded.id, fields: extracted.fields } }));
    setCorrections(current => ({ ...current, [fdrId]: Object.fromEntries(Object.entries(extracted.fields).map(([key, field]) => [key, field.value])) }));
    await refresh();
    setNotice(`PDF stored with SHA-256 ${uploaded.sha256}. Review extracted values before confirming them.`);
  }

  async function createRecord(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(null); setNotice(null);
    const file = (event.currentTarget.elements.namedItem("supportingPdf") as HTMLInputElement | null)?.files?.[0];
    try {
      const record = await sendJson<Fdr>("/api/fdrs/", "POST", {
        institution_id: institutionId, fdr_number: fdrNumber, bank_name: bankName,
        branch_name: branchName, principal_amount: amount, issue_date: issueDate,
        maturity_date: maturityDate, purpose, renewal_of: renewalOf || null,
      });
      if (file) await uploadDocument(record.id, file);
      setFdrNumber(""); setBankName(""); setBranchName(""); setAmount(""); setIssueDate(""); setMaturityDate(""); setPurpose(""); setRenewalOf("");
      await refresh(); setNotice(`Draft ${record.fdr_number} created.`);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not create FDR."); }
    finally { setBusy(false); }
  }

  async function confirmDocument(fdrId: string) {
    const current = extraction[fdrId];
    if (!current) return;
    setBusy(true); setError(null);
    try {
      await sendJson(`/api/fdrs/${fdrId}/documents/${current.documentId}/confirm/`, "POST", { fields: corrections[fdrId] ?? {} });
      await refresh(); setNotice("Document observations saved. The values are now part of the record history.");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not confirm observations."); }
    finally { setBusy(false); }
  }

  async function submitRecord(id: string) {
    if (!window.confirm("Submit this FDR for review? You may need to provide additional evidence if verification identifies a discrepancy.")) return;
    setBusy(true); setError(null);
    try { await sendJson(`/api/fdrs/${id}/submit/`, "POST", {}); await refresh(); setNotice("FDR submitted with verified document observations."); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not submit FDR."); }
    finally { setBusy(false); }
  }

  async function openVersions(id: string) {
    try {
      const versions = await request<Array<{ version_number: number; change_reason: string; valid_from: string; snapshot: Record<string, unknown> }>>(`/api/fdrs/${id}/versions/`);
      setSelectedVersions(current => ({ ...current, [id]: versions }));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load record history."); }
  }

  async function loadPassport(id: string) {
    try {
      const detail = await request<{ evidence: EvidenceRow[] }>(`/api/fdrs/${id}/`);
      setPassports(current => ({ ...current, [id]: detail.evidence ?? [] }));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load evidence provenance."); }
  }

  async function downloadDocument(fdrId: string, doc: DocumentRow) {
    try {
      const response = await fetch(`${api}/api/fdrs/${fdrId}/documents/${doc.id}/download/`, { credentials: "include" });
      if (!response.ok) throw new Error("Download is not available for this record.");
      const blob = await response.blob(); const url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url; link.download = doc.filename; link.click(); URL.revokeObjectURL(url);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not download document."); }
  }

  async function importBankBatch(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); const file = (event.currentTarget.elements.namedItem("bankCsv") as HTMLInputElement | null)?.files?.[0];
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const token = await csrf(); const form = new FormData(); form.append("file", file);
      const result = await request<{ id: string; row_count: number; classifications: Record<string, number> }>("/api/bank-batches/", { method: "POST", headers: { "X-CSRFToken": token }, body: form });
      const detail = await request<{ results: BatchResult[] }>(`/api/bank-batches/${result.id}/results/`);
      setBatchResults(detail.results); await refresh(); setNotice(`Processed ${result.row_count} bank rows; ${Object.entries(result.classifications).map(([kind, count]) => `${kind}: ${count}`).join(" | ")}`);
      setTab("reconciliation");
    } catch (e) { setError(e instanceof Error ? e.message : "Bank CSV could not be processed."); }
    finally { setBusy(false); }
  }

  async function updateCase(item: FdrCase, patch: Record<string, unknown>) {
    setBusy(true); setError(null);
    try { await sendJson(`/api/cases/${item.id}/`, "PATCH", patch); await refresh(); setNotice(`Case ${item.id} updated.`); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not update case."); }
    finally { setBusy(false); }
  }

  async function markAlertRead(alert: Alert) {
    try { await sendJson(`/api/case-alerts/${alert.id}/read/`, "POST", {}); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not mark alert read."); }
  }

  return <section className="mx-auto mt-5 max-w-6xl">
    {error && <p role="alert" className="mt-5 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    {notice && <p role="status" className="mt-5 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</p>}
    {loading && <p role="status" className="animate-pulse rounded-lg bg-white p-4 text-sm text-slate-600">Loading secure records and work queues…</p>}

    {tab === "records" && <div className="mt-6 grid gap-5">
      {canCreate && presentation !== "repository" && presentation !== "passport" && <Card><CardContent><h3 className="font-bold">Create FDR draft</h3><form onSubmit={createRecord} className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label className="text-xs font-semibold">Institution<select required value={institutionId} onChange={e => setInstitutionId(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"><option value="">Select institution</option>{institutions.map(item => <option key={item.id} value={item.id}>{item.code} - {item.name}</option>)}</select></label>
        <label className="text-xs font-semibold">FDR number<input required value={fdrNumber} onChange={e => setFdrNumber(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
        <label className="text-xs font-semibold">Bank name<input required value={bankName} onChange={e => setBankName(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
        <label className="text-xs font-semibold">Branch<input value={branchName} onChange={e => setBranchName(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
        <label className="text-xs font-semibold">Principal amount<input required type="number" min="0.01" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
        <label className="text-xs font-semibold">Issue date<input required type="date" value={issueDate} onChange={e => setIssueDate(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
        <label className="text-xs font-semibold">Maturity date<input required type="date" value={maturityDate} onChange={e => setMaturityDate(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
        <label className="text-xs font-semibold">Purpose<input value={purpose} onChange={e => setPurpose(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
        <label className="text-xs font-semibold">Renewal of (optional)<select value={renewalOf} onChange={e => setRenewalOf(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"><option value="">New FDR</option>{records.filter(row => row.status === "SUBMITTED" && row.maturity_date <= new Date().toISOString().slice(0, 10)).map(row => <option key={row.id} value={row.id}>{row.fdr_number}</option>)}</select></label>
        <label className="text-xs font-semibold">Supporting PDF (optional)<input name="supportingPdf" type="file" accept="application/pdf,.pdf" className="mt-1 block w-full text-sm" /></label>
        <div className="sm:col-span-2 lg:col-span-3"><Button disabled={busy || institutions.length === 0}>{busy ? "Saving..." : "Create draft"}</Button></div>
      </form></CardContent></Card>}

      {(records.length > 0 || presentation === "repository" || presentation === "passport") && <div className="flex flex-wrap items-end justify-between gap-3 rounded-xl border border-slate-200 bg-white p-3 shadow-sm"><label className="min-w-[220px] flex-1 text-xs font-semibold text-slate-600">Search records<input value={recordQuery} onChange={event => setRecordQuery(event.target.value)} placeholder="FDR number, bank, or branch" className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"/></label><label className="text-xs font-semibold text-slate-600">Status<select value={recordStatus} onChange={event => setRecordStatus(event.target.value)} className="mt-1 block min-w-40 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"><option value="ALL">All statuses</option>{Array.from(new Set(records.map(row => row.status))).sort().map(status => <option key={status} value={status}>{status.replaceAll("_", " ")}</option>)}</select></label><span className="pb-2 text-xs text-slate-500">{visibleRecords.length} of {records.length} records</span></div>}
      {records.length === 0 && <Card><CardContent><p className="text-sm text-slate-500">No FDR records are visible for this account yet.</p></CardContent></Card>}
      {records.length > 0 && visibleRecords.length === 0 && <div className="portal-empty">No records match the current search and status filter.</div>}
      {visibleRecords.map(row => <Card key={row.id}><CardContent>
        <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex flex-wrap items-center gap-2"><h3 className="text-lg font-bold">{row.fdr_number}</h3><span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold">{row.status.replaceAll("_", " ")}</span>{row.reverification_required && <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-900">Re-verification required</span>}</div><p className="mt-1 text-sm text-slate-500">{row.bank_name} | INR {Number(row.principal_amount).toLocaleString("en-IN")} | Matures {row.maturity_date}</p></div><div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" aria-expanded={Boolean(passports[row.id])} onClick={() => passports[row.id] ? setPassports(current => { const next = { ...current }; delete next[row.id]; return next; }) : void loadPassport(row.id)}>{passports[row.id] ? "Close Evidence Passport" : "Evidence Passport"}</Button><Button size="sm" variant="outline" onClick={() => void openVersions(row.id)}>History</Button>{["DRAFT", "NEEDS_REVERIFICATION"].includes(row.status) && <Button size="sm" onClick={() => void submitRecord(row.id)} disabled={busy}>Submit</Button>}</div></div>
        {passports[row.id] && <div className="mt-4 rounded-xl border border-blue-100 bg-blue-50/60 p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><h4 className="font-bold text-ink">Evidence Passport</h4><p className="mt-1 text-xs text-slate-600">Each value is attributed to its evidence source. Supporting documents alone are not bank confirmation.</p></div><span className="rounded-full bg-white px-2.5 py-1 text-xs font-bold text-slate-700">{passports[row.id].length} current evidence entries</span></div>{passports[row.id].length ? <div className="mt-3 grid gap-2 xl:grid-cols-2">{passports[row.id].map(item => <div key={item.public_id} className="rounded-lg border border-slate-200 bg-white p-3"><div className="flex flex-wrap items-center justify-between gap-2"><span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${item.evidence_type === "BANK_CONFIRMED" ? "bg-emerald-100 text-emerald-900" : item.evidence_type === "DOCUMENT_OBSERVED" ? "bg-blue-100 text-blue-900" : "bg-slate-100 text-slate-800"}`}>{item.evidence_type.replaceAll("_", " ")}</span><time className="text-[10px] text-slate-500">{new Date(item.recorded_at).toLocaleString()}</time></div><p className="mt-2 text-xs font-semibold uppercase text-slate-500">{item.field_name.replaceAll("_", " ")}</p><p className="mt-1 break-words text-sm font-medium">{typeof item.value === "string" ? item.value : JSON.stringify(item.value)}</p><p className="mt-1 text-[10px] text-slate-500">Confidence: {item.confidence ?? "not scored"}{item.corrected_by_user ? " · Corrected by an official" : ""}</p></div>)}</div> : <p className="mt-3 text-sm text-slate-600">No current evidence entries are recorded for this FDR.</p>}</div>}
        <div className="mt-4 grid gap-4 md:grid-cols-[1fr_1fr]">
          <div><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Supporting documents ({(documents[row.id] ?? []).length})</p><label className="mt-2 block text-xs text-slate-600">Upload PDF<input type="file" accept="application/pdf,.pdf" disabled={busy} onChange={e => { const file = e.target.files?.[0]; if (file) void uploadDocument(row.id, file).catch(err => setError(err instanceof Error ? err.message : "Upload failed.")); e.currentTarget.value = ""; }} className="mt-1 block w-full text-sm" /></label>
            <ul className="mt-3 space-y-2">{(documents[row.id] ?? []).map(doc => <li key={doc.id} className="rounded-lg bg-slate-50 p-3 text-xs"><div className="flex flex-wrap justify-between gap-2"><button onClick={() => void downloadDocument(row.id, doc)} className="font-semibold text-teal underline">{doc.filename}</button><span>{formatBytes(doc.size_bytes)}</span></div><p className="mt-1 break-all font-mono text-[10px] text-slate-400">SHA-256 {doc.sha256}</p></li>)}</ul>
          </div>
          {extraction[row.id] && <div className="rounded-xl border border-teal/20 bg-teal/5 p-4"><p className="text-xs font-bold uppercase tracking-widest text-teal">Review extracted document values</p><div className="mt-3 space-y-2">{Object.entries(extraction[row.id].fields).map(([field, data]) => <label key={field} className="grid grid-cols-[1fr_90px] items-center gap-2 text-xs"><span className="font-semibold">{field.replaceAll("_", " ")} <span className="font-normal text-slate-500">{Math.round(data.confidence * 100)}% confidence</span></span><input value={corrections[row.id]?.[field] ?? data.value} onChange={e => setCorrections(current => ({ ...current, [row.id]: { ...current[row.id], [field]: e.target.value } }))} className="col-span-2 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm" /></label>)}</div><Button size="sm" className="mt-3" disabled={busy} onClick={() => void confirmDocument(row.id)}>Confirm observations</Button></div>}
        </div>
        {selectedVersions[row.id] && <div className="mt-4 rounded-lg bg-slate-50 p-4"><h4 className="text-sm font-bold">Record versions</h4><ol className="mt-2 space-y-2 text-xs">{selectedVersions[row.id].map(version => <li key={version.version_number}><strong>Version {version.version_number}</strong> | {version.change_reason || "Record updated"} | {new Date(version.valid_from).toLocaleString()}</li>)}</ol></div>}
      </CardContent></Card>)}
    </div>}

    {tab === "reconciliation" && <div className="mt-6 grid gap-5">
      {canImport(role) && <Card><CardContent><h3 className="font-bold">Process bank simulator batch</h3><p className="mt-1 text-sm text-slate-500">CSV requires FDR number and principal amount. Optional columns: bank, branch, issue date, maturity date.</p><form onSubmit={event => void importBankBatch(event)} className="mt-4 flex flex-wrap items-center gap-3"><input name="bankCsv" required type="file" accept="text/csv,.csv" className="text-sm"/><Button disabled={busy}>{busy ? "Reconciling..." : "Upload and reconcile"}</Button></form></CardContent></Card>}
      <Card><CardContent><div className="flex items-center justify-between"><h3 className="font-bold">Duplicate findings</h3><span className="text-sm text-slate-500">{duplicates.length} group(s)</span></div>{duplicates.length ? <ul className="mt-3 space-y-2">{duplicates.map((item, index) => <li key={`${item.type}-${index}`} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm"><strong>{item.type.replaceAll("_", " ")}</strong>: {item.normalized_value ?? item.sha256}<p className="mt-1 text-xs text-slate-600">{item.records.map(record => `${record.fdr_number} (${record.institution})`).join(" | ")}</p></li>)}</ul> : <p className="mt-3 text-sm text-slate-500">No duplicate FDR numbers or document hashes found in your visible records.</p>}</CardContent></Card>
      <Card><CardContent><h3 className="font-bold">Reconciliation results</h3>{batchResults.length ? <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[600px] text-left text-sm"><thead className="text-xs uppercase text-slate-400"><tr><th className="py-2">FDR number</th><th>Classification</th><th>Bank row</th><th>Details</th></tr></thead><tbody>{batchResults.map(row => <tr key={row.id} className="border-t border-slate-100"><td className="py-2 font-medium">{row.fdr_number ?? "-"}</td><td><span className="rounded-full bg-slate-100 px-2 py-1 text-xs">{row.classification.replaceAll("_", " ")}</span></td><td>{row.bank_row_number ?? "-"}</td><td className="max-w-sm truncate text-xs text-slate-500">{JSON.stringify(row.details)}</td></tr>)}</tbody></table></div> : <p className="mt-3 text-sm text-slate-500">No reconciliation results yet.</p>}</CardContent></Card>
    </div>}

    {tab === "cases" && <div className="mt-6 grid gap-4">
      <Card><CardContent><div className="flex items-center justify-between"><h3 className="font-bold">Exception cases</h3><span className="text-sm text-slate-500">{openCases.length} open</span></div>{cases.length ? <div className="mt-4 space-y-3">{cases.map(item => <CaseCard key={item.id} item={item} staff={staff} canManage={canManageCases} busy={busy} onUpdate={patch => updateCase(item, patch)} />)}</div> : <p className="mt-3 text-sm text-slate-500">No exception cases have been created.</p>}</CardContent></Card>
      <Card><CardContent><div className="flex items-center justify-between"><h3 className="font-bold">Your case alerts</h3><span className="text-sm text-slate-500">{alerts.filter(alert => !alert.read).length} unread</span></div>{alerts.length ? <ul className="mt-3 space-y-2">{alerts.map(alert => <li key={alert.id} className={`flex items-start justify-between gap-3 rounded-lg p-3 text-sm ${alert.read ? "bg-slate-50" : "bg-blue-50"}`}><span>{alert.message}<span className="mt-1 block text-xs text-slate-400">{new Date(alert.created_at).toLocaleString()}</span></span>{!alert.read && <Button size="sm" variant="outline" onClick={() => void markAlertRead(alert)}>Mark read</Button>}</li>)}</ul> : <p className="mt-3 text-sm text-slate-500">No reminders or escalations for this account.</p>}</CardContent></Card>
    </div>}
    {tab === "oversight" && <Phase45Console role={role} />}
  </section>;
}

function CaseCard({ item, staff, canManage, busy, onUpdate }: { item: FdrCase; staff: StaffUser[]; canManage: boolean; busy: boolean; onUpdate: (patch: Record<string, unknown>) => void }) {
  const [closureEvidence, setClosureEvidence] = useState(item.closure_evidence);
  const [ownerId, setOwnerId] = useState(item.owner ? String(item.owner.id) : "");
  const [severity, setSeverity] = useState(item.severity);
  const [dueAt, setDueAt] = useState(item.due_at.slice(0, 16));
  return <article className="rounded-xl border border-slate-200 p-4"><div className="flex flex-wrap justify-between gap-2"><div><p className="text-xs font-bold uppercase tracking-widest text-slate-400">{item.case_type.replaceAll("_", " ")} | {item.severity}</p><h4 className="mt-1 font-bold">{item.title}</h4><p className="mt-1 text-sm text-slate-600">{item.description}</p><p className="mt-2 text-xs text-slate-500">FDR {item.fdr_number ?? "-"} | Owner {item.owner?.username ?? "Unassigned"} | Due {new Date(item.due_at).toLocaleString()} | {item.escalation_count} escalation(s)</p></div><span className="h-fit rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold">{item.status.replaceAll("_", " ")}</span></div>
    {item.status !== "CLOSED" && item.can_update && <div className="mt-4 grid gap-3 sm:grid-cols-2">{canManage && <><label className="text-xs font-semibold">Assign owner<select value={ownerId} onChange={e => setOwnerId(e.target.value)} className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-sm"><option value="">Unassigned</option>{staff.map(user => <option key={user.id} value={user.id}>{user.username} - {user.role.replaceAll("_", " ")}</option>)}</select></label><label className="text-xs font-semibold">Severity<select value={severity} onChange={e => setSeverity(e.target.value)} className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-sm">{["LOW", "MEDIUM", "HIGH", "CRITICAL"].map(value => <option key={value}>{value}</option>)}</select></label><label className="text-xs font-semibold">Due date<input type="datetime-local" value={dueAt} onChange={e => setDueAt(e.target.value)} className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label><Button size="sm" variant="outline" disabled={busy} onClick={() => onUpdate({ owner_id: ownerId || null, severity, due_at: new Date(dueAt).toISOString() })}>Save case details</Button></>}{item.status === "OPEN" && <Button size="sm" variant="outline" disabled={busy} onClick={() => onUpdate({ status: "IN_PROGRESS" })}>Start case</Button>}<label className="text-xs font-semibold sm:col-span-2">Closure evidence<input value={closureEvidence} onChange={e => setClosureEvidence(e.target.value)} placeholder="Describe the evidence resolving this case" className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label><Button size="sm" className="sm:col-span-2 sm:justify-self-start" disabled={busy || closureEvidence.trim().length < 10} onClick={() => onUpdate({ status: "CLOSED", closure_evidence: closureEvidence })}>Close with evidence</Button></div>}
  </article>;
}


