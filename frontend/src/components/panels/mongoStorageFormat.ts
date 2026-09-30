const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

export function formatBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n) || n < 0) return "-";
  if (n === 0) return "0 B";

  const unitIndex = Math.min(
    Math.floor(Math.log(n) / Math.log(1024)),
    BYTE_UNITS.length - 1,
  );
  const value = n / 1024 ** unitIndex;
  const formattedValue =
    unitIndex === 0 || value >= 10
      ? value.toFixed(0)
      : value.toFixed(1).replace(/\.0$/, "");

  return `${formattedValue} ${BYTE_UNITS[unitIndex]}`;
}
