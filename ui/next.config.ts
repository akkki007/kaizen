import type { NextConfig } from "next";

// The browser talks to this origin only; the backend is reached through rewrites so the
// session cookie stays first-party. API_URL is the backend (compose service or localhost).
const API_URL = process.env.API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${API_URL}/api/v1/:path*` },
      { source: "/auth/:path*", destination: `${API_URL}/auth/:path*` },
    ];
  },
};

export default config;
