import type { NextConfig } from "next";
const apiOrigin = (process.env.BACKEND_API_ORIGIN ?? process.env.RENDER_API_ORIGIN)?.replace(/\/+$/, "");
if (apiOrigin && !apiOrigin.startsWith("https://")) {
  throw new Error("BACKEND_API_ORIGIN must be an HTTPS URL when configured.");
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async rewrites() {
    // Allow the frontend to deploy before the API exists. Once the API is
    // deployed, set BACKEND_API_ORIGIN in Vercel and redeploy to enable proxying.
    return apiOrigin
      ? [{ source: "/backend/api/:path*", destination: `${apiOrigin}/api/:path*` }]
      : [];
  },
};
export default nextConfig;
