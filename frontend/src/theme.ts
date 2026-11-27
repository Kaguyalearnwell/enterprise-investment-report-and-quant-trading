// Chart palette (validated reference palette, light/dark steps). Taiwan convention: up = red, down = green;
// candles always carry the legend label, so identity is never colour-only.
export const palette = {
  light: {
    surface: "#ffffff", text: "#191919", text2: "#666666", grid: "#e6e6e6",
    series: ["#257de0", "#f47230", "#00b894", "#f2a600", "#ee7fac", "#008a17", "#6a4ce2", "#ec4647"],
    up: "#d93f3f", down: "#1f8a3b", band: "rgba(37,125,224,0.08)", neutral: "#9c9c9c",
  },
  dark: {
    surface: "#191919", text: "#efefef", text2: "#9c9c9c", grid: "#333333",
    series: ["#338ced", "#e76624", "#00a888", "#d28e05", "#e24d8b", "#0aac23", "#9887ef", "#eb6263"],
    up: "#eb6263", down: "#0aac23", band: "rgba(51,140,237,0.12)", neutral: "#6b6b6b",
  },
};
export type Pal = typeof palette.light;
