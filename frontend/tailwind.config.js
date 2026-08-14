import frappeUIPreset from "frappe-ui/src/tailwind/preset.js";

export default {
  presets: [frappeUIPreset],
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts}",
    "./node_modules/frappe-ui/src/components/**/*.{vue,js,ts}",
  ],
};
