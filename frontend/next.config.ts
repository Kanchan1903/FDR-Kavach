import type { NextConfig } from "next";
const apiOrigin = process.env.RENDER_API_ORIGIN?.replace(/\/+$/, "");
if (process.env.VERCEL && !apiOrigin?.startsWith("https://")) {
  throw new Error("Set RENDER_API_ORIGIN to the HTTPS URL of the Render API service.");
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async rewrites() {
    return apiOrigin
      ? [{ source: "/backend/api/:path*", destination: `${apiOrigin}/api/:path*` }]
      : [];
  },
};
export default nextConfig;
