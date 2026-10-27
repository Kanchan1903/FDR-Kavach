import type { Config } from "tailwindcss";
const config: Config = { content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"], theme: { extend: { colors: { ink: "#1F497D", teal: "#087e8b", canvas: "#f3f6f8" } } }, plugins: [] };
export default config;
