import path from "path";
import { fileURLToPath } from "url";

const frontendDir = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  turbopack: {
    root: frontendDir,
  },
  // Proxy API calls through Next dev server so /health works from the browser
  // without cross-origin issues (localhost:3000 → 127.0.0.1:8000).
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: "http://127.0.0.1:8000/:path*",
      },
    ];
  },
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;