import path from "node:path";
import type { NextConfig } from "next";

// Next infers the workspace root from the nearest lockfile. A stray
// package.json/package-lock.json/node_modules sits in C:\Users\xyreo (left over
// from a one-off `npm install pptxgenjs`), so Next selected the entire user
// profile as the root. Turbopack then scanned and watched that whole tree --
// OneDrive, Documents, Downloads, AppData and every node_modules on the machine --
// which exhausted kernel handles and failed writes with
// "Insufficient system resources exist to complete the requested service"
// (os error 1450), panicking Turbopack and serving 500s.
//
// Pinning the root confines it to this app. Do not remove without also removing
// the stray lockfile in the user profile.
const projectRoot = path.resolve(__dirname);

const nextConfig: NextConfig = {
  /* config options here */
  reactCompiler: true,
  allowedDevOrigins: ['127.0.0.1'],
  turbopack: {
    root: projectRoot,
  },
};

export default nextConfig;
