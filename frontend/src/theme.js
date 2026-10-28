export const DEFAULT_PORTAL_APPEARANCE = Object.freeze({
  header: "#163D2A",
  primary: "#1D5A3C",
  page: "#F5F7F6",
  page_accent: "#E3F0E8",
  card: "#FFFFFF",
  highlight: "#F4FAF6",
  label: "#E8F1EB",
});

const HEX_COLOR = /^#[0-9A-F]{6}$/;

export function isValidThemeColor(value) {
  return HEX_COLOR.test(String(value || "").trim().toUpperCase());
}

export function normalizePortalAppearance(appearance = {}) {
  return Object.fromEntries(Object.entries(DEFAULT_PORTAL_APPEARANCE).map(([key, fallback]) => {
    const value = String(appearance?.[key] || "").trim().toUpperCase();
    return [key, isValidThemeColor(value) ? value : fallback];
  }));
}

function rgb(hex) {
  return [1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16));
}

function hex(channels) {
  return `#${channels.map((channel) => Math.round(channel).toString(16).padStart(2, "0")).join("")}`.toUpperCase();
}

function mix(first, second, secondWeight) {
  const a = rgb(first), b = rgb(second);
  return hex(a.map((channel, index) => channel * (1 - secondWeight) + b[index] * secondWeight));
}

function luminance(value) {
  const channels = rgb(value).map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(first, second) {
  const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

function readableText(background) {
  const dark = "#17211B", light = "#FFFFFF";
  return contrast(dark, background) >= contrast(light, background) ? dark : light;
}

export function portalThemeVariables(appearance = {}) {
  const palette = normalizePortalAppearance(appearance);
  const isShippedPalette = Object.keys(DEFAULT_PORTAL_APPEARANCE).every((key) => palette[key] === DEFAULT_PORTAL_APPEARANCE[key]);
  if (isShippedPalette) return {
    "--portal-header": palette.header,
    "--portal-header-text": "#FFFFFF",
    "--portal-header-muted": "#C9DDD1",
    "--portal-primary": palette.primary,
    "--portal-primary-text": "#FFFFFF",
    "--portal-primary-hover": "#164B31",
    "--portal-page": palette.page,
    "--portal-page-accent": palette.page_accent,
    "--portal-page-text": "#17211B",
    "--portal-page-muted": "#66756D",
    "--portal-card": palette.card,
    "--portal-card-text": "#17211B",
    "--portal-card-muted": "#66756D",
    "--portal-border": "#DFE7E2",
    "--portal-highlight": palette.highlight,
    "--portal-highlight-text": "#17211B",
    "--portal-highlight-muted": "#66756D",
    "--portal-highlight-border": "#BDD3C5",
    "--portal-label": palette.label,
    "--portal-label-text": "#275139",
    "--portal-accent-on-card": palette.primary,
  };
  const headerText = readableText(palette.header);
  const primaryText = readableText(palette.primary);
  const pageText = readableText(palette.page);
  const cardText = readableText(palette.card);
  const highlightText = readableText(palette.highlight);
  const labelText = readableText(palette.label);
  const accentOnCard = contrast(palette.primary, palette.card) >= 4.5 ? palette.primary : cardText;
  return {
    "--portal-header": palette.header,
    "--portal-header-text": headerText,
    "--portal-header-muted": mix(headerText, palette.header, 0.25),
    "--portal-primary": palette.primary,
    "--portal-primary-text": primaryText,
    "--portal-primary-hover": mix(palette.primary, primaryText === "#FFFFFF" ? "#000000" : "#FFFFFF", 0.12),
    "--portal-page": palette.page,
    "--portal-page-accent": palette.page_accent,
    "--portal-page-text": pageText,
    "--portal-page-muted": mix(pageText, palette.page, 0.35),
    "--portal-card": palette.card,
    "--portal-card-text": cardText,
    "--portal-card-muted": mix(cardText, palette.card, 0.35),
    "--portal-border": mix(cardText, palette.card, 0.85),
    "--portal-highlight": palette.highlight,
    "--portal-highlight-text": highlightText,
    "--portal-highlight-muted": mix(highlightText, palette.highlight, 0.35),
    "--portal-highlight-border": mix(highlightText, palette.highlight, 0.80),
    "--portal-label": palette.label,
    "--portal-label-text": labelText,
    "--portal-accent-on-card": accentOnCard,
  };
}

export function applyPortalTheme(appearance = {}) {
  const variables = portalThemeVariables(appearance);
  for (const [name, value] of Object.entries(variables)) document.documentElement.style.setProperty(name, value);
  return normalizePortalAppearance(appearance);
}
