"use client";

import { CheckCircle2, KeyRound, LockKeyhole, ShieldCheck } from "lucide-react";

type Identity = {
  username: string;
  role: string;
  mfa_enabled: boolean;
  mfa_required: boolean;
  institution: { code: string; name: string } | null;
};

const descriptions: Record<string, string> = {
  INSTITUTION_USER: "Institution-scoped record access and FDR submission. The API limits records to your linked institution.",
  REVIEWER: "Review and reconciliation access. Release review actions are recorded separately from approval.",
  APPROVER: "Independent release approval access. The API prevents approving a request you made or reviewed.",
  SENIOR_OFFICER: "Operational oversight, case management, bank batch imports, and eligible release workflow actions.",
  ADMIN: "Administration, Rule Set configuration, operational access, and audit verification.",
  AUDITOR: "Read oversight data and verify audit integrity. Audit verification is restricted to administrators and auditors.",
};

export default function AccessControls({ identity }: { identity: Identity }) {
  const roleName = identity.role.replaceAll("_", " ");
  const mfaLabel = identity.mfa_enabled
    ? identity.mfa_required ? "Enabled · required for this role" : "Enabled · optional for this role"
    : identity.mfa_required ? "Required · enrollment is incomplete" : "Not enabled · optional for this role";
  const statusClasses = identity.mfa_enabled
    ? "border-emerald-200 bg-emerald-50 text-emerald-950"
    : identity.mfa_required ? "border-red-200 bg-red-50 text-red-950" : "border-amber-200 bg-amber-50 text-amber-950";

  return <section className="grid gap-5 xl:grid-cols-2">
    <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center gap-3"><div className="grid h-10 w-10 place-items-center rounded-lg bg-blue-50 text-ink"><ShieldCheck size={20}/></div><div><h2 className="font-bold">Role-based access control</h2><p className="text-xs text-slate-500">Enforced by the Django API on each request</p></div></div>
      <div className="mt-5 rounded-xl bg-slate-50 p-4"><p className="text-xs font-bold uppercase tracking-wider text-slate-500">Your signed-in account</p><p className="mt-2 text-lg font-bold">{identity.username}</p><p className="mt-1 inline-flex rounded-full bg-blue-100 px-2.5 py-1 text-xs font-bold text-ink">{roleName}</p>{identity.institution && <p className="mt-3 text-sm text-slate-700">Institution scope: <strong>{identity.institution.code} · {identity.institution.name}</strong></p>}</div>
      <h3 className="mt-5 text-sm font-bold">Role access summary</h3><p className="mt-1 text-sm leading-6 text-slate-600">{descriptions[identity.role] ?? "Access is determined by your server-assigned role."}</p>
      <div className="mt-4 space-y-2 text-xs text-slate-600"><p className="flex items-start gap-2"><CheckCircle2 size={15} className="mt-0.5 shrink-0 text-emerald-700"/>Navigation and available actions reflect the signed-in role.</p><p className="flex items-start gap-2"><CheckCircle2 size={15} className="mt-0.5 shrink-0 text-emerald-700"/>The API independently checks role and object scope; hiding a button is not the authorization boundary.</p><p className="flex items-start gap-2"><CheckCircle2 size={15} className="mt-0.5 shrink-0 text-emerald-700"/>Institution users are restricted to their institution’s records; cross-institution record lookups return 404.</p></div>
    </article>
    <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center gap-3"><div className="grid h-10 w-10 place-items-center rounded-lg bg-blue-50 text-ink"><KeyRound size={20}/></div><div><h2 className="font-bold">Multi-factor authentication</h2><p className="text-xs text-slate-500">Authenticator-based TOTP</p></div></div>
      <div className={`mt-5 rounded-xl border p-4 ${statusClasses}`}><p className="text-xs font-bold uppercase tracking-wider">Account MFA status</p><p className="mt-2 font-bold">{mfaLabel}</p><p className="mt-1 text-xs">{identity.mfa_required ? "A password alone cannot complete sign-in for this account." : "Institution MFA can be enabled by the administrator policy."}</p></div>
      <div className="mt-5 rounded-xl border border-slate-200 p-4"><h3 className="flex items-center gap-2 text-sm font-bold"><LockKeyhole size={16}/> Sign-in policy</h3><ul className="mt-3 space-y-2 text-sm leading-6 text-slate-600"><li>Reviewer, approver, senior officer, administrator, and auditor accounts require TOTP verification.</li><li>Staff accounts enroll an authenticator during first sign-in, then verify a fresh code on subsequent sign-ins.</li><li>Institution-user MFA is optional by default and becomes mandatory when the administrator enables the institution MFA setting.</li></ul></div>
      <p className="mt-4 text-xs leading-5 text-slate-500">MFA status is read from your authenticated account record. This page does not reveal your TOTP secret.</p>
    </article>
  </section>;
}
