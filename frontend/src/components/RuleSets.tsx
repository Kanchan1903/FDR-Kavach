"use client";

import { useEffect, useState } from "react";

const api = process.env.NODE_ENV === "production" ? "/backend" : (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000");
type Rule = {
  id: string;
  name: string;
  required_amount: string;
  shortfall_tolerance: string;
  source: string;
  effective_from: string | null;
  approving_authority: string;
  active: boolean;
  checklist_items: string[];
};

export default function RuleSets({ role }: { role: string }) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [source, setSource] = useState("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [authority, setAuthority] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const response = await fetch(`${api}/api/coverage/rules/`, { credentials: "include" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? `Could not load Rule Sets (${response.status}).`);
      setRules(payload);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load Rule Sets.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function createRule(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const csrf = await fetch(`${api}/api/csrf/`, { credentials: "include" }).then(response => response.json());
      const response = await fetch(`${api}/api/coverage/rules/`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf.csrfToken },
        body: JSON.stringify({
          name,
          required_amount: amount,
          source,
          effective_from: effectiveFrom,
          approving_authority: authority,
          active: true,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? `Could not save Rule Set (${response.status}).`);
      setName(""); setAmount(""); setSource(""); setEffectiveFrom(""); setAuthority("");
      setNotice("Active Rule Set saved. The previous active set has been deactivated.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save Rule Set.");
    } finally {
      setSaving(false);
    }
  }

  return <section aria-labelledby="rules-title" className="space-y-6">
    <div><p className="text-xs font-bold uppercase tracking-[.18em] text-teal">Administration</p><h2 id="rules-title" className="mt-2 text-2xl font-bold">Coverage Rule Sets</h2><p className="mt-1 text-sm text-slate-600">Every active coverage value needs its source, effective date, and approving authority.</p></div>
    {error && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">{error}</p>}
    {notice && <p role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">{notice}</p>}
    {loading ? <div className="animate-pulse rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500" role="status">Loading Rule Sets…</div> : rules.length ? <div className="grid gap-3">{rules.map(rule => <article key={rule.id} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-bold">{rule.name}</h3><p className="mt-1 text-sm text-slate-600">Required: ₹{Number(rule.required_amount).toLocaleString("en-IN")} · Tolerance: ₹{Number(rule.shortfall_tolerance).toLocaleString("en-IN")}</p></div><span className={`rounded-full px-3 py-1 text-xs font-bold ${rule.active ? "bg-emerald-100 text-emerald-900" : "bg-slate-100 text-slate-700"}`}>{rule.active ? "ACTIVE" : "INACTIVE"}</span></div>
      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3"><div><dt className="text-xs font-semibold uppercase text-slate-500">Source</dt><dd className="mt-1">{rule.source || "Not recorded"}</dd></div><div><dt className="text-xs font-semibold uppercase text-slate-500">Effective from</dt><dd className="mt-1">{rule.effective_from ?? "Not recorded"}</dd></div><div><dt className="text-xs font-semibold uppercase text-slate-500">Approving authority</dt><dd className="mt-1">{rule.approving_authority || "Not recorded"}</dd></div></dl>
      {rule.source.toUpperCase().includes("DEMO RULE") && <p className="mt-3 inline-flex rounded bg-amber-100 px-2.5 py-1 text-xs font-bold text-amber-950">DEMO RULE · Not an official requirement</p>}
    </article>)}</div> : <div className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-600">No Rule Sets configured. Coverage cannot be calculated until an administrator adds one.</div>}
    {role === "ADMIN" && <form onSubmit={createRule} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><h3 className="font-bold">Create active Rule Set</h3><p className="mt-1 text-xs text-slate-500">Entering a value here does not make it an official AICTE rule. Record an authoritative source and approval.</p><div className="mt-4 grid gap-4 sm:grid-cols-2">
      <label className="text-sm font-semibold">Rule Set name<input required maxLength={120} value={name} onChange={e => setName(e.target.value)} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
      <label className="text-sm font-semibold">Required coverage amount<input required type="number" min="0.01" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
      <label className="text-sm font-semibold sm:col-span-2">Source / reference<input required maxLength={500} value={source} onChange={e => setSource(e.target.value)} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
      <label className="text-sm font-semibold">Effective date<input required type="date" value={effectiveFrom} onChange={e => setEffectiveFrom(e.target.value)} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
      <label className="text-sm font-semibold">Approving authority<input required maxLength={255} value={authority} onChange={e => setAuthority(e.target.value)} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2" /></label>
    </div><button disabled={saving} className="mt-5 rounded-lg bg-ink px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-60">{saving ? "Saving…" : "Save active Rule Set"}</button></form>}
    {role !== "ADMIN" && <p className="rounded-lg bg-blue-50 p-4 text-sm text-blue-950">Rule Set changes are restricted to administrators. Read access remains available for oversight.</p>}
  </section>;
}
