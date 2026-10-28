"use client";

import { useEffect, useState } from "react";
import {
  Activity, Archive, ArrowRight, BriefcaseBusiness, ChartNoAxesCombined, ChevronDown, ClipboardCheck,
  Eye, EyeOff, FileClock, FilePlus2, FileSearch, Fingerprint, Landmark, LayoutDashboard, LogOut, Menu,
  BookOpen, CircleHelp, LockKeyhole, Moon, RefreshCw, Settings2, Shield, ShieldAlert, ShieldCheck, Sun, X,
} from "lucide-react";
import OperationsConsole from "@/components/OperationsConsole";
import Phase45Console from "@/components/Phase45Console";
import RuleSets from "@/components/RuleSets";
import AccessControls from "@/components/AccessControls";

// Production authentication relies on the same-origin proxy so the browser
// can retain Django's secure session and CSRF cookies. Do not let a public
// NEXT_PUBLIC_API_URL override bypass that proxy on Vercel.
const api = process.env.NODE_ENV === "production" ? "/backend" : (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000");
const demoMode = process.env.NEXT_PUBLIC_DEMO_MODE === "true";
const demoPassword = demoMode ? "DemoKavach2026!" : "";
const demoProfiles = [
  { label: "Institution 1", username: "demo-demo-001", group: "Institutions" },
  { label: "Institution 2", username: "demo-demo-002", group: "Institutions" },
  { label: "Institution 3", username: "demo-demo-003", group: "Institutions" },
  { label: "Reviewer", username: "demo-reviewer", group: "AICTE staff roles" },
  { label: "Approver", username: "demo-approver", group: "AICTE staff roles" },
  { label: "Senior Officer", username: "demo-senior", group: "AICTE staff roles" },
  { label: "Administrator", username: "demo-admin", group: "AICTE staff roles" },
  { label: "Auditor", username: "demo-auditor", group: "AICTE staff roles" },
];
type Identity = { id: string; username: string; role: string; mfa_enabled: boolean; mfa_required: boolean; institution: { code: string; name: string } | null };
type VerifyResult = { valid: boolean; checked: number; head_hash?: string; failed_event_id?: string | number };
type PageKey = "dashboard" | "repository" | "passport" | "submission" | "reconciliation" | "cases" | "coverage" | "release" | "history" | "audit" | "rules" | "impact" | "security";

const pageInfo: Record<PageKey, { label: string; description: string; icon: typeof LayoutDashboard }> = {
  dashboard: { label: "Dashboard", description: "Portfolio overview, outstanding risks, and prioritized work.", icon: LayoutDashboard },
  repository: { label: "FDR repository", description: "Search and review institution-scoped fixed deposit records.", icon: Archive },
  passport: { label: "Evidence & Documents", description: "Review submitted documents, extracted fields, and evidence provenance.", icon: FileSearch },
  submission: { label: "FDR submission", description: "Create a draft, attach a supporting PDF, and submit it for review.", icon: FilePlus2 },
  reconciliation: { label: "Bank reconciliation", description: "Review simulated CSV matches, discrepancies, and unmapped records.", icon: Landmark },
  cases: { label: "Case Centre", description: "Assign owners, manage due dates, and record closure evidence.", icon: BriefcaseBusiness },
  coverage: { label: "Coverage timeline", description: "Compare required, submitted, and bank-confirmed deposits by day.", icon: Activity },
  release: { label: "Release readiness", description: "Review checklist status and follow the maker-checker release process.", icon: ClipboardCheck },
  history: { label: "Historical Time Machine", description: "Review what was valid and what officials knew at a point in time.", icon: FileClock },
  audit: { label: "Audit & integrity", description: "Verify the append-only, tamper-evident audit history.", icon: Fingerprint },
  rules: { label: "Administration", description: "Manage sourced coverage values, effective dates, and approvals.", icon: Settings2 },
  impact: { label: "Reports", description: "Review verification turnaround, outstanding risks, and export reports.", icon: ChartNoAxesCombined },
  security: { label: "Access & MFA", description: "See your assigned portal role, data scope, and multi-factor authentication status.", icon: LockKeyhole },
};

const navigationGroups: Array<{ label: string; items: Array<{ key: PageKey; roles?: string[] }> }> = [
  { label: "Overview", items: [{ key: "dashboard" }, { key: "impact", roles: ["REVIEWER", "APPROVER", "SENIOR_OFFICER", "ADMIN", "AUDITOR"] }] },
  { label: "Records", items: [{ key: "repository" }, { key: "passport" }, { key: "submission", roles: ["INSTITUTION_USER", "SENIOR_OFFICER", "ADMIN"] }] },
  { label: "Operations", items: [
    { key: "reconciliation", roles: ["REVIEWER", "SENIOR_OFFICER", "ADMIN", "AUDITOR"] },
    { key: "cases", roles: ["INSTITUTION_USER", "REVIEWER", "SENIOR_OFFICER", "ADMIN"] },
    { key: "coverage" }, { key: "release" },
  ] },
  { label: "Governance", items: [
    { key: "history" }, { key: "security" }, { key: "audit", roles: ["ADMIN", "AUDITOR"] },
    { key: "rules", roles: ["REVIEWER", "APPROVER", "SENIOR_OFFICER", "ADMIN", "AUDITOR"] },
  ] },
];

async function getCsrf() {
  const response = await fetch(`${api}/api/csrf/`, { credentials: "include" });
  if (!response.ok) {
    throw new Error(`Secure session request failed (HTTP ${response.status}). The latest frontend build must use the /backend API proxy.`);
  }
  const payload = await response.json();
  if (typeof payload.csrfToken !== "string" || !payload.csrfToken) {
    throw new Error("Secure session response did not include a CSRF token. Check the API connection.");
  }
  return payload.csrfToken as string;
}

export default function Home() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [page, setPage] = useState<PageKey>("dashboard");
  const [username, setUsername] = useState("");
  const [usernameMode, setUsernameMode] = useState<"manual" | "demo">("manual");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [code, setCode] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  const [mfaMode, setMfaMode] = useState<"verify" | "enroll" | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [demoCodeLoading, setDemoCodeLoading] = useState(false);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [dark, setDark] = useState(false);
  const [audit, setAudit] = useState<VerifyResult | null>(null);
  const [authChecking, setAuthChecking] = useState(true);

  async function refreshIdentity() {
    const response = await fetch(`${api}/api/auth/me/`, { credentials: "include" });
    if (response.ok) setIdentity(await response.json());
    else setIdentity(null);
  }

  useEffect(() => {
    void refreshIdentity().catch(() => setIdentity(null)).finally(() => setAuthChecking(false));
    setDark(window.localStorage.getItem("fdr-kavach-theme") === "dark");
    const fragment = window.location.hash.slice(1) as PageKey;
    if (fragment in pageInfo) setPage(fragment);
  }, []);
  useEffect(() => { document.documentElement.dataset.theme = dark ? "dark" : "light"; window.localStorage.setItem("fdr-kavach-theme", dark ? "dark" : "light"); }, [dark]);
  useEffect(() => { if (mfaMode === "enroll") void loadMfaEnrollment(); }, [mfaMode]);
  useEffect(() => { if (mfaMode === "verify" && demoMode) void autofillDemoCode(); }, [mfaMode, username]);
  useEffect(() => { if (identity && page === "audit" && ["ADMIN", "AUDITOR"].includes(identity.role)) void verifyAudit(); }, [identity, page]);

  async function signIn(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      const csrf = await getCsrf();
      const response = await fetch(`${api}/api/auth/login/`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ username, password }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Sign in failed.");
      setPassword("");
      if (payload.mfa_enrollment_required) { setMfaMode("enroll"); setNotice("Set up an authenticator app to complete staff sign in."); }
      else if (payload.mfa_required) { setMfaMode("verify"); setNotice("Enter the current code from your authenticator app."); }
      else { await refreshIdentity(); setNotice("Signed in securely."); }
    } catch (e) { setError(e instanceof Error ? e.message : "Sign in failed."); }
    finally { setBusy(false); }
  }

  async function loadMfaEnrollment() {
    try {
      const response = await fetch(`${api}/api/auth/mfa/enroll/`, { credentials: "include" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Could not start MFA enrollment.");
      setSecret(payload.secret);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not start MFA enrollment."); }
  }

  async function autofillDemoCode() {
    if (!demoMode || !demoProfiles.some(profile => profile.username === username)) return;
    setDemoCodeLoading(true);
    try {
      const response = await fetch(`${api}/api/auth/demo-totp/?username=${encodeURIComponent(username)}`, { credentials: "include" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Demo authenticator code is unavailable. Use your authenticator app.");
      setCode(payload.code);
      setNotice(`Current demo authenticator code filled · valid for about ${payload.valid_for_seconds} seconds.`);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not fill the demo authenticator code.");
    } finally {
      setDemoCodeLoading(false);
    }
  }

  async function submitMfa(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const csrf = await getCsrf();
      const endpoint = mfaMode === "enroll" ? "enroll" : "verify";
      const response = await fetch(`${api}/api/auth/mfa/${endpoint}/`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ code }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "MFA verification failed.");
      setCode(""); setSecret(null); setMfaMode(null); await refreshIdentity(); setNotice("Identity verified. You are signed in.");
    } catch (e) { setError(e instanceof Error ? e.message : "MFA verification failed."); }
    finally { setBusy(false); }
  }

  async function signOut() {
    setBusy(true);
    try { const csrf = await getCsrf(); await fetch(`${api}/api/auth/logout/`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: "{}" }); setIdentity(null); setAudit(null); setNotice("You have been signed out."); }
    catch (e) { setError(e instanceof Error ? e.message : "Sign out failed."); }
    finally { setBusy(false); }
  }

  async function verifyAudit() {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${api}/api/audit/verify/`, { credentials: "include" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Audit verification is unavailable.");
      setAudit(payload);
    } catch (e) { setError(e instanceof Error ? e.message : "Audit verification is unavailable."); setAudit(null); }
    finally { setBusy(false); }
  }

  function navigate(next: PageKey) {
    setPage(next); setMobileMenu(false); setError(""); setNotice("");
    window.history.replaceState(null, "", `#${next}`);
  }

  const visibleGroups = navigationGroups.map(group => ({ ...group, items: group.items.filter(item => !item.roles || item.roles.includes(identity?.role ?? "")) })).filter(group => group.items.length > 0);
  const metadata = pageInfo[page];
  const PageIcon = metadata.icon;
  const phaseView = page === "coverage" ? "coverage" : page === "release" ? "release" : page === "history" ? "history" : page === "impact" ? "impact" : "dashboard";

  if (authChecking) return <main className="grid min-h-screen place-items-center bg-[#f2f5f8] text-slate-700"><p role="status" className="animate-pulse rounded-lg bg-white px-5 py-4 shadow-sm">Checking secure session...</p></main>;
  if (!identity) return <main id="main-content" className="login-page min-h-screen text-slate-900">
    <div className="login-topbar mx-auto flex w-full max-w-[1800px] items-center justify-between gap-4 px-5 py-4 sm:px-9 lg:px-12">
      <div className="flex items-center gap-4">
        <div className="seal-mark grid h-12 w-12 shrink-0 place-items-center rounded-full border border-ink/20 bg-white/80 text-ink sm:h-14 sm:w-14"><Landmark size={27} strokeWidth={1.8}/></div>
        <div className="hidden h-12 w-px bg-ink/25 sm:block"/>
        <div><p className="text-[10px] font-bold uppercase tracking-[.28em] text-slate-500 sm:text-xs">Government of India</p><h1 className="mt-1 text-sm font-bold text-ink sm:text-base">AICTE Regulation Bureau</h1><div className="mt-2 h-[3px] w-9 rounded-full bg-ink"/></div>
      </div>
      <div className="flex items-center gap-2 text-[11px] font-semibold text-ink sm:gap-6 sm:text-sm"><span className="inline-flex items-center gap-2"><CircleHelp size={17}/> <span className="hidden sm:inline">Help &amp; Support</span></span><span className="hidden h-5 w-px bg-blue-200 sm:block"/><span className="inline-flex items-center gap-2"><BookOpen size={17}/> <span className="hidden sm:inline">User Guide</span></span></div>
    </div>

    <div className="login-layout mx-auto grid w-full max-w-[1800px] items-center gap-8 px-5 py-5 sm:px-9 lg:px-12 xl:min-h-[calc(100vh-200px)] xl:grid-cols-[1.12fr_.88fr] xl:gap-10 xl:py-0">
      <section className="login-hero relative flex min-h-[360px] flex-col justify-between overflow-hidden rounded-[26px] px-5 py-6 sm:px-8 xl:min-h-[600px] xl:rounded-none xl:bg-transparent xl:px-0 xl:py-7">
        <InstitutionalIllustration/>
        <div className="relative z-10 max-w-[590px] xl:pt-5">
          <div className="flex items-center gap-4 sm:gap-5"><div><p className="login-wordmark">FDR Kavach</p><p className="mt-1 text-sm font-medium tracking-wide text-slate-600 sm:text-lg">Security Deposit Management Portal</p></div><div className="login-shield grid h-[64px] w-[58px] shrink-0 place-items-center text-white sm:h-[82px] sm:w-[74px]"><Shield size={82} strokeWidth={1.4} className="absolute h-[78px] w-[78px] text-teal sm:h-[92px] sm:w-[92px]"/><Landmark size={25} className="relative z-10 text-white sm:h-8 sm:w-8"/></div></div>
          <div className="mt-5 h-[3px] w-12 rounded-full bg-teal"/>
          <h2 className="login-promise mt-4 max-w-[450px] text-[27px] font-semibold leading-[1.12] text-ink sm:text-[34px]">Protected records.<br/>Verified evidence.<br/>Controlled release.</h2>
          <p className="mt-4 max-w-[460px] text-sm leading-6 text-slate-600 sm:text-base sm:leading-7">A secure platform for managing fixed deposit records, verifying evidence, reconciling bank data, and controlling release approvals across institutions.</p>
        </div>
        <div className="login-illustration absolute right-[-3%] top-[25%] z-0 hidden h-[300px] w-[54%] items-center justify-center 2xl:flex">
          <div className="illustration-orbit absolute h-[280px] w-[280px] rounded-full border border-dashed border-blue-300/70"/>
          <div className="document-stack relative z-10 flex h-[220px] w-[290px] rotate-[-3deg] items-center justify-center rounded-[24px] border border-white/90 bg-white/75 shadow-[0_22px_55px_rgba(31,73,125,.16)] backdrop-blur-sm">
            <div className="absolute -left-5 top-5 h-[180px] w-[240px] rotate-[-7deg] rounded-2xl border border-blue-100 bg-blue-100/80 shadow-lg"/>
            <div className="relative h-[180px] w-[230px] rounded-xl border border-blue-100 bg-white px-6 py-5 shadow-md"><div className="flex items-center gap-2 text-ink"><FileSearch size={21}/><span className="font-serif text-2xl font-bold tracking-wide">FDR</span></div><div className="mt-5 space-y-2.5"><span className="block h-2 w-4/5 rounded-full bg-blue-100"/><span className="block h-2 w-full rounded-full bg-slate-100"/><span className="block h-2 w-3/4 rounded-full bg-slate-100"/></div><span className="absolute -bottom-2 right-5 h-12 w-3 rotate-[-8deg] rounded-full bg-ink/90"/></div>
            <div className="absolute -right-5 -bottom-2 grid h-16 w-16 place-items-center rounded-full border-4 border-white bg-teal text-white shadow-lg"><ShieldCheck size={31}/></div>
          </div>
          <div className="absolute right-[-2%] top-[10%] flex items-center gap-3 rounded-xl border border-white/80 bg-white/90 px-3 py-2 shadow-sm backdrop-blur-sm"><span className="grid h-10 w-10 place-items-center rounded-lg bg-blue-50 text-ink"><Landmark size={21}/></span><span className="text-[10px] font-bold uppercase leading-4 tracking-wide text-slate-600">Bank<br/>verification</span></div>
          <div className="absolute bottom-[8%] right-[2%] flex items-center gap-3 rounded-xl border border-white/80 bg-white/90 px-3 py-2 shadow-sm backdrop-blur-sm"><span className="grid h-10 w-10 place-items-center rounded-lg bg-teal/10 text-teal"><ClipboardCheck size={21}/></span><span className="text-[10px] font-bold uppercase leading-4 tracking-wide text-slate-600">Controlled<br/>release</span></div>
        </div>
        <div className="relative z-10 mt-6 grid gap-2.5 sm:grid-cols-3 sm:gap-3 xl:mt-0"><Feature icon={<FileSearch size={17}/>} title="Evidence records" description="Upload, extract and verify FDR details"/><Feature icon={<RefreshCw size={17}/>} title="Bank reconciliation" description="Match and identify discrepancies"/><Feature icon={<ShieldCheck size={17}/>} title="Independent approvals" description="Maker-checker release workflow"/></div>
      </section>

      <section className="flex w-full items-center justify-center xl:py-5">
        <div className="login-card w-full max-w-[620px] rounded-[24px] border border-white/90 bg-white/95 p-5 shadow-[0_22px_70px_rgba(31,73,125,.12)] backdrop-blur-sm sm:p-8 lg:p-9">
          <div className="flex items-start justify-between gap-4"><div><p className="text-[10px] font-bold uppercase tracking-[.24em] text-[#4779aa] sm:text-xs">Authorized access</p><h2 className="login-card-title mt-2 text-[25px] font-semibold leading-tight text-ink sm:text-[29px]">{mfaMode ? (mfaMode === "enroll" ? "Set up your authenticator" : "Verify your identity") : "Sign in to FDR Kavach"}</h2></div>{demoMode && <div className="shrink-0 text-right"><span className="inline-flex items-center gap-1.5 rounded-md bg-orange-100 px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-wide text-orange-800"><span className="h-2 w-2 rounded-full bg-orange-500"/>Demo mode</span><span className="mt-1 block text-[10px] font-medium text-orange-700">Simulated bank data</span></div>}</div>
          <p className="mt-3 max-w-md text-sm leading-6 text-slate-600">{mfaMode ? (mfaMode === "enroll" ? "Register an authenticator app to complete secure staff sign-in." : "Enter the current code from your registered authenticator app.") : "Use your authorized credentials to access the AICTE Security Deposit Management Portal."}</p>
          {error && <p role="alert" className="mt-5 rounded-xl border border-red-200 bg-red-50 p-3.5 text-sm text-red-900">{error}</p>}{notice && <p role="status" className="mt-5 rounded-xl border border-blue-200 bg-blue-50 p-3.5 text-sm text-blue-950">{notice}</p>}
          {!mfaMode ? <form onSubmit={signIn} className="mt-5 space-y-4">
            <label className="block text-sm font-bold text-slate-800">Username{demoMode && <select aria-label="Username or seeded demo account" autoComplete="username" required value={usernameMode === "demo" ? username : "__manual__"} onChange={e => { const value = e.target.value; setCode(""); setError(""); setNotice(""); if (value === "__manual__") { setUsernameMode("manual"); setUsername(""); setPassword(""); } else { setUsernameMode("demo"); setUsername(value); setPassword(demoPassword); } }} className="mt-2 w-full rounded-xl border border-blue-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 outline-none focus:border-[#1f497d] focus:ring-2 focus:ring-blue-100"><option value="__manual__">Enter username manually</option><optgroup label="Institutions">{demoProfiles.filter(profile => profile.group === "Institutions").map(profile => <option key={profile.username} value={profile.username}>{profile.label} · {profile.username}</option>)}</optgroup><optgroup label="AICTE staff roles">{demoProfiles.filter(profile => profile.group === "AICTE staff roles").map(profile => <option key={profile.username} value={profile.username}>{profile.label} · {profile.username}</option>)}</optgroup></select>}{(!demoMode || usernameMode === "manual") && <div className="login-input-wrap mt-2"><Landmark size={18} aria-hidden="true"/><input autoComplete="username" required value={username} onChange={e => setUsername(e.target.value)} placeholder="Enter your username" className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-slate-400"/></div>}</label>
            <label className="block text-sm font-bold text-slate-800">Password<div className="login-input-wrap mt-2"><LockKeyhole size={18} aria-hidden="true"/><input type={showPassword ? "text" : "password"} autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} placeholder="Enter your password" className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-slate-400"/><button type="button" aria-label={showPassword ? "Hide password" : "Show password"} onClick={() => setShowPassword(value => !value)} className="rounded-md p-1 text-slate-500 hover:bg-blue-50 hover:text-ink">{showPassword ? <EyeOff size={18}/> : <Eye size={18}/>}</button></div></label>
            <button disabled={busy} className="group mt-2 flex w-full items-center justify-center gap-3 rounded-xl bg-gradient-to-r from-[#173f70] to-[#1f5489] px-5 py-4 text-sm font-bold text-white shadow-[0_7px_18px_rgba(31,73,125,.18)] transition hover:from-[#12365f] hover:to-[#194775] disabled:cursor-wait disabled:opacity-60">{busy ? <RefreshCw size={17} className="animate-spin"/> : <LockKeyhole size={17}/>}{busy ? "Signing in securely..." : "Sign in securely"}<ArrowRight size={17} className="transition-transform group-hover:translate-x-1"/></button>
          </form> : <form onSubmit={submitMfa} className="mt-5 space-y-4">{mfaMode === "enroll" && secret && <p className="rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm leading-6 text-slate-700">Add this setup key to your authenticator app:<code className="mt-2 block select-all break-all font-mono font-bold text-ink">{secret}</code></p>}<label className="block text-sm font-bold text-slate-800">Six-digit authenticator code<div className="login-input-wrap mt-2"><ShieldCheck size={18}/><input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={code} onChange={e => setCode(e.target.value)} placeholder="000000" className="min-w-0 flex-1 bg-transparent font-mono text-lg tracking-[.35em] outline-none placeholder:text-slate-300"/></div></label>{demoMode && demoProfiles.some(profile => profile.username === username) && <button type="button" onClick={() => void autofillDemoCode()} disabled={demoCodeLoading} className="text-sm font-semibold text-[#1f497d] underline decoration-blue-300 underline-offset-4 disabled:opacity-60">{demoCodeLoading ? "Refreshing demo code..." : "Fill demo authenticator code"}</button>}<button disabled={busy} className="flex w-full items-center justify-center gap-3 rounded-xl bg-ink px-5 py-4 text-sm font-bold text-white disabled:opacity-60">{busy ? <RefreshCw size={17} className="animate-spin"/> : <ShieldCheck size={17}/>}{busy ? "Verifying..." : mfaMode === "enroll" ? "Confirm and sign in" : "Verify and continue"}<ArrowRight size={17}/></button></form>}
          <div className="mt-6 flex items-start gap-3 rounded-xl bg-[#f2f6fa] px-4 py-3.5"><ShieldCheck size={19} className="mt-0.5 shrink-0 text-ink"/><p className="text-xs leading-5 text-slate-600">Access is monitored and recorded. Never share your password or authenticator code.</p></div>
        </div>
      </section>
    </div>
  </main>;

  return <div className="portal-shell min-h-screen bg-canvas text-slate-900">
    <header className="portal-header sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur">
      <div className="portal-header-inner flex h-[68px] items-center justify-between px-4 sm:px-6 lg:px-8">
        <div className="portal-brand flex min-w-0 items-center gap-3"><button aria-label={mobileMenu ? "Close navigation" : "Open navigation"} onClick={() => setMobileMenu(value => !value)} className="rounded-lg p-2 text-slate-700 hover:bg-slate-100 md:hidden">{mobileMenu ? <X size={20}/> : <Menu size={20}/>}</button><div className="brand-mark grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-ink text-white"><Landmark size={21}/></div><div className="brand-name min-w-0"><p className="truncate text-[10px] font-bold uppercase tracking-[.12em] text-slate-500">AICTE Regulation Bureau</p><p className="truncate text-sm font-bold text-ink">FDR Kavach</p></div><span className="brand-divider"/><span className="brand-product">Security Deposit<br className="hidden sm:block"/> Management Portal</span></div>
        <div className="portal-user-area flex items-center gap-2 sm:gap-4"><button aria-label={dark ? "Use light theme" : "Use dark theme"} onClick={() => setDark(value => !value)} className="header-icon-button">{dark ? <Sun size={17}/> : <Moon size={17}/>}</button><span className="profile-avatar" aria-hidden="true">{identity.username.slice(0, 2).toUpperCase()}</span><div className="hidden text-left sm:block"><p className="text-xs font-bold text-ink">{identity.username}</p><p className="text-[10px] uppercase tracking-wide text-slate-500">{identity.role.replaceAll("_", " ")}</p></div><button onClick={() => void signOut()} disabled={busy} className="signout-button inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"><LogOut size={15}/><span className="hidden sm:inline">Sign out</span></button></div>
      </div>
    </header>
    <div className="portal-layout">
      <aside className="portal-sidebar hidden md:block">
        <nav aria-label="Main navigation">{visibleGroups.map(group => <div className="nav-group" key={group.label}><p className="nav-group-label">{group.label}</p>{group.items.map(item => <NavButton key={item.key} itemKey={item.key} current={page} onSelect={navigate}/>)}</div>)}</nav>
        <div className="sidebar-security"><ShieldCheck size={16}/><span>Role-scoped access<br/><small>Actions are audit logged</small></span></div>
      </aside>
      {mobileMenu && <div className="mobile-nav-scrim md:hidden" onClick={() => setMobileMenu(false)}><nav aria-label="Mobile navigation" className="mobile-nav" onClick={event => event.stopPropagation()}><div className="mb-4 flex items-center justify-between"><span className="font-bold text-ink">Portal navigation</span><button aria-label="Close navigation" onClick={() => setMobileMenu(false)} className="rounded p-2"><X size={18}/></button></div>{visibleGroups.map(group => <div className="nav-group" key={group.label}><p className="nav-group-label">{group.label}</p>{group.items.map(item => <NavButton key={item.key} itemKey={item.key} current={page} onSelect={navigate}/>)}</div>)}</nav></div>}
      <main id="main-content" className="portal-main min-w-0 flex-1">
        <div className="page-heading-row"><div className="page-heading-copy"><div className="page-icon"><PageIcon size={20}/></div><div><p className="eyebrow">AICTE Regulation Bureau</p><div className="flex flex-wrap items-center gap-3"><h1 className="page-title">{metadata.label}</h1>{demoMode && <span className="demo-pill"><span/>DEMO · Simulated data</span>}</div><p className="page-description">{metadata.description}</p></div></div></div>
      {error && <p role="alert" className="mb-5 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">{error}</p>}{notice && <p role="status" className="mb-5 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">{notice}</p>}
        {page === "audit" ? <AuditPage result={audit} error={error} loading={busy} onVerify={() => void verifyAudit()} role={identity.role}/> : page === "security" ? <AccessControls identity={identity}/> : page === "rules" ? <RuleSets role={identity.role}/> : page === "reconciliation" ? <OperationsConsole key={page} role={identity.role} initialTab="reconciliation"/> : page === "cases" ? <OperationsConsole key={page} role={identity.role} initialTab="cases"/> : ["repository", "passport", "submission"].includes(page) ? <OperationsConsole key={page} role={identity.role} initialTab="records" presentation={page as "repository" | "passport" | "submission"}/> : <Phase45Console key={page} role={identity.role} username={identity.username} view={phaseView}/>}
      </main>
    </div>
  </div>;
}

function NavButton({ itemKey, current, onSelect }: { itemKey: PageKey; current: PageKey; onSelect: (page: PageKey) => void }) {
  const info = pageInfo[itemKey]; const Icon = info.icon; const selected = current === itemKey;
  return <button aria-current={selected ? "page" : undefined} onClick={() => onSelect(itemKey)} className={`portal-nav-item ${selected ? "is-active" : ""}`}><Icon size={17} aria-hidden="true"/><span className="min-w-0 flex-1">{info.label}</span>{selected && <ChevronDown size={14} className="-rotate-90" aria-hidden="true"/>}</button>;
}


function Feature({ icon, title, description }: { icon: React.ReactNode; title: string; description: string }) {
  return <div className="login-feature flex min-h-[76px] items-center gap-3 rounded-xl border p-3"><span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-blue-50 text-ink">{icon}</span><span><span className="block text-xs font-bold text-ink sm:text-sm">{title}</span><span className="mt-1 hidden text-[11px] leading-4 text-slate-600 sm:block">{description}</span></span></div>;
}

function InstitutionalIllustration() {
  return <div className="institution-backdrop" aria-hidden="true"><svg viewBox="0 0 960 640" preserveAspectRatio="xMidYMid slice" className="h-full w-full">
    <defs><linearGradient id="gov-sky" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#d9e9f7" stopOpacity=".12"/><stop offset=".55" stopColor="#9bc2df" stopOpacity=".34"/><stop offset="1" stopColor="#e8f3fa" stopOpacity=".1"/></linearGradient><linearGradient id="gov-stone" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#6d99bd" stopOpacity=".22"/><stop offset="1" stopColor="#3e739e" stopOpacity=".1"/></linearGradient><filter id="gov-soften"><feGaussianBlur stdDeviation="1.3"/></filter></defs>
    <path d="M0 470 170 270l112 90 142-205 115 110L730 62l230 170v408H0Z" fill="url(#gov-sky)"/>
    <g filter="url(#gov-soften)" fill="url(#gov-stone)" stroke="#5888ad" strokeOpacity=".18" strokeWidth="2"><path d="M545 300c0-80 55-145 135-160 80 15 135 80 135 160v35H545Z"/><path d="M615 165c0-52 28-90 65-115 37 25 65 63 65 115Z"/><path d="M663 52h34V30h-34z"/><path d="M570 335h220v28H570z"/><path d="M548 365h265l42 34H510z"/><path d="M500 400h360v16H500z"/><path d="M518 416h324v92H518z"/><path d="M545 415h22v93h-22zM603 415h22v93h-22zM661 415h22v93h-22zM719 415h22v93h-22zM777 415h22v93h-22z"/><path d="M485 508h390v24H485zM450 532h460v20H450zM425 550h500v16H425zM398 566h554v20H398zM433 585h485v16H433z"/></g>
    <g fill="#5e91b4" fillOpacity=".12"><circle cx="222" cy="420" r="95"/><circle cx="320" cy="466" r="70"/><circle cx="845" cy="475" r="88"/></g>
  </svg><div className="institution-haze"/></div>;
}

function AuditPage({ result, error, loading, onVerify, role }: { result: VerifyResult | null; error: string; loading: boolean; onVerify: () => void; role: string }) {
  if (!["ADMIN", "AUDITOR"].includes(role)) return <div className="rounded-xl border border-blue-100 bg-blue-50 p-5 text-sm text-blue-950">Audit integrity verification is restricted to administrators and auditors. The API enforces this permission.</div>;
  return <section className="grid gap-5 xl:grid-cols-[1.4fr_.6fr]">
    <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8"><div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs font-bold uppercase tracking-[.15em] text-slate-500">Chain verification</p><h2 className="mt-2 text-xl font-bold">Audit integrity status</h2><p className="mt-1 max-w-2xl text-sm text-slate-600">Verify the linked history of security-sensitive activity. A valid result confirms the stored event chain passed this check.</p></div><button onClick={onVerify} disabled={loading} className="inline-flex items-center gap-2 rounded-lg bg-ink px-4 py-2.5 text-sm font-bold text-white disabled:opacity-60"><RefreshCw size={15} className={loading ? "animate-spin" : ""}/>{loading ? "Verifying..." : "Verify chain"}</button></div>
      {error && <p role="alert" className="mt-5 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">{error}</p>}{!result && !error && <p role="status" className="mt-6 animate-pulse rounded-lg bg-slate-50 p-5 text-sm text-slate-500">Checking audit chain?</p>}
      {result && <div className={`mt-6 rounded-xl border p-5 ${result.valid ? "border-emerald-200 bg-emerald-50" : "border-red-200 bg-red-50"}`}><div className="flex items-start gap-3">{result.valid ? <ShieldCheck className="text-emerald-700"/> : <ShieldAlert className="text-red-700"/>}<div><h3 className="font-bold">{result.valid ? "Audit chain verified" : `Integrity failure at event ${result.failed_event_id ?? "unknown"}`}</h3><p className="mt-1 text-sm">{result.checked} event{result.checked === 1 ? "" : "s"} checked.</p></div></div>{result.head_hash && <p className="mt-4 break-all font-mono text-xs text-slate-600">Chain head: {result.head_hash}</p>}</div>}
    </article>
    <aside className="rounded-2xl bg-ink p-6 text-white"><p className="text-xs font-bold uppercase tracking-[.15em] text-blue-100">Integrity model</p><h2 className="mt-3 text-xl font-bold">Append-only, tamper-evident</h2><p className="mt-3 text-sm leading-6 text-blue-100">Each audit event links to the preceding event through a cryptographic hash. Application users cannot edit or delete recorded events. Verification can identify changes to the chain.</p><p className="mt-4 rounded-lg border border-white/20 bg-white/10 p-3 text-xs leading-5 text-blue-50">A database administrator could rewrite the full chain. Production deployments should export signed checkpoints to independently controlled storage.</p></aside>
  </section>;
}
