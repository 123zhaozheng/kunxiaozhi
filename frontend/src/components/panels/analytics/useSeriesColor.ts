import { useTheme } from "../../../contexts/ThemeContext";
import { seriesColor } from "./analyticsFormat";

/** Series hue for the active theme (dark mode uses its own selected step). */
export function useSeriesColor(): string {
  const { theme } = useTheme();
  return seriesColor(theme === "dark");
}
