export type InventoryLoadIntent =
  | { type: "refresh" }
  | { type: "navigate"; page: number }
  | { type: "filters-changed" };

export function resolveInventoryPage(
  currentPage: number,
  intent: InventoryLoadIntent,
): number {
  if (intent.type === "filters-changed") return 0;
  if (intent.type === "navigate") return Math.max(0, intent.page);
  return currentPage;
}
