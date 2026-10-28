import type { NextConfig } from "next";
// Vercel hosts the public SIH demo. Its public API hostname and demo UI are
// defaults so an unset dashboard variable does not silently break the demo.
// Set BACKEND_API_ORIGIN and NEXT_PUBLIC_DEMO_MODE explicitly to override.
const apiOrigin = (
  process.env.BACKEND_API_ORIGIN ||
  process.env.RENDER_API_ORIGIN ||
  (process.env.VERCEL ? "https://fdr-kavach-api.onrender.com" : undefined)
)?.replace(/\/+$/, "");
if (apiOrigin && !apiOrigin.startsWith("https://")) {
  throw new Error("BACKEND_API_ORIGIN must be an HTTPS URL when configured.");
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  env: {
    NEXT_PUBLIC_DEMO_MODE: process.env.NEXT_PUBLIC_DEMO_MODE ?? (process.env.VERCEL ? "true" : "false"),
  },
  async rewrites() {
    // Allow the frontend to deploy before the API exists. Once the API is
    // deployed, set BACKEND_API_ORIGIN in Vercel and redeploy to enable proxying.
    return apiOrigin
      ? [{ source: "/backend/api/:path*", destination: `${apiOrigin}/api/:path*` }]
      : [];
  },
};
export default nextConfig;
