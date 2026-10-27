import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "FDR Kavach | Security Deposit Oversight", description: "Protected records. Verified evidence. Controlled release." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en" data-theme="light"><body><a className="skip-link" href="#main-content">Skip to main content</a>{children}</body></html>; }
