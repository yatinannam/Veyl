import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  reactCompiler: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  experimental: {
    // /api/* passes through src/proxy.ts, which buffers request bodies up to this size.
    // Above the API's 10 MB upload limit plus multipart overhead, so the API decides.
    proxyClientMaxBodySize: "12mb",
  },
};

export default nextConfig;
