# Security policy and threat model

## Scope and reporting

This is a development and hackathon prototype, not an approved production service. Do not upload real FDRs, account details, or personal information. Report a suspected vulnerability privately to the project maintainers with a concise reproduction, affected endpoint, and impact. Do not include live credentials or full account numbers in a report.

## Assets

- Institution identity and FDR financial/validity data.
- Uploaded FDR evidence, bank batch files, parsed results, and audit history.
- User credentials, session cookies, CSRF tokens, and TOTP seeds.
- Rule sets, case decisions, coverage calculations, and release/discharge evidence.

## Trust boundaries and adversaries

The browser, institution users, uploaded files, and imported bank CSVs are untrusted. Staff roles are privileged but remain distinct for reviewer and approver actions. The API, PostgreSQL, Redis, private document storage, and deployment secrets are separate trust zones. Relevant threats include a compromised institution account attempting cross-institution access; malicious or malformed uploads; stolen sessions; credential stuffing; an ordinary user trying to forge bank evidence, approve their own release, or rewrite audit history; and a privileged infrastructure operator able to modify stored data.

## Controls implemented

- Server-side role and object authorization. Institution users are scoped by institution foreign key; unauthorized cross-institution record lookups return 404. Public object identifiers are UUIDs.
- Django server-side sessions rotate at authentication and use HttpOnly/SameSite cookies; unsafe requests require CSRF. Login responses are generic, attempts are throttled, and accounts lock after configured failures.
- Argon2 is the preferred password hasher. Staff roles must enroll/use TOTP; institution MFA is configurable. TOTP secrets are encrypted at rest.
- Production settings reject weak/default secrets, absent encryption keys, insecure session/CSRF cookies, disabled HTTPS redirect, and non-HTTPS web/CSRF origins. `DEBUG` defaults off.
- PDF uploads validate extension, size, signature, parser acceptance, page bounds, encryption, and active-content markers. Bank CSV has size, encoding, schema, numeric/date and row-count validation. Files use random server-generated names in private storage and are encrypted at rest. Downloads are authenticated and object-scoped.
- User-submitted documents create document-observed evidence only. Only processed bank batch rows create bank-confirmed evidence.
- Maker-checker rules block requester self-review/self-approval and require distinct reviewer/approver identities. Material changes invalidate active approvals and trigger re-verification.
- Audit events reject updates/deletes through application models and use a verifiable hash chain. Errors are sanitized; secrets and account details are not intentionally logged.
- CI config runs Ruff, Bandit, pip-audit, npm audit, frontend build, tests/coverage, and Gitleaks.

## Residual risks and limitations

- A database administrator or host operator can rewrite the entire audit chain and its head. It is append-only, tamper-evident at the application layer, not tamper-proof. Deployments should export signed checkpoints to separately administered storage.
- Key management is environment-variable based in this prototype; there is no KMS integration or automated key rotation/re-encryption workflow.
- Encryption covers uploaded evidence/bank files and TOTP seeds. The model currently has no dedicated beneficiary/account fields; any future fields need encryption and redaction from logs/exports.
- The bank verifier is a simulator. It does not authenticate a real bank source. A CSV upload is only as trustworthy as its provenance.
- PDF extraction is text based and does not OCR. Active-content rejection is defense in depth, not a complete PDF malware scanner. Production deployments should add isolated scanning/quarantine and content-disarm controls.
- No external SMS/email provider is connected. Alerts are persisted and escalated in app only.
- Demo rules and records are synthetic; no seeded value represents an official AICTE requirement. Administrators must enter source, effective date, value and approving authority for configured rules.
- The app still needs a deployment review: TLS termination, secrets manager, backups/restore drills, independent audit retention, incident response, monitoring, and operational access controls.

## Demo data

`make seed` runs only when both `DJANGO_DEBUG=1` and `DEMO_MODE=true`. It creates synthetic institutions, users, FDRs and deliberately mismatched bank CSV results. It prints generated passwords only when it creates demo accounts. Do not enable demo mode or use demo credentials in a shared or production environment.
