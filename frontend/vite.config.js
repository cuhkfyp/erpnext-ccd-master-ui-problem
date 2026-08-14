import vue from "@vitejs/plugin-vue";
import frappeui from "frappe-ui/vite";
import fs from "node:fs";
import path from "path";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    frappeui({
      frappeProxy: true,
      lucideIcons: true,
      frappeTypes: false,
      jinjaBootData: true,
      buildConfig: {
        outDir: "../ccd_portal/public/ccd-portal",
        emptyOutDir: true,
        sourcemap: false,
        indexHtmlPath: "../ccd_portal/www/ccd_portal.html",
      },
    }),
    vue({
      script: {
        fs: {
          fileExists: fs.existsSync,
          readFile: (file) => fs.readFileSync(file, "utf-8"),
        },
      },
    }),
  ],
  base: "/assets/ccd_portal/ccd-portal/",
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
});
