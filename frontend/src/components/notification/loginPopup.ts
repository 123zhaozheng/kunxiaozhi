export type PopupCandidate = {
  id: string;
  should_popup?: boolean;
};

export function getPopupEligibleItems<T extends PopupCandidate>(
  items: T[],
): T[] {
  return items.filter((item) => item.should_popup);
}

export function shouldAutoOpenNotifications(
  items: PopupCandidate[],
): boolean {
  return getPopupEligibleItems(items).length > 0;
}

export function getIdsToSnooze(items: PopupCandidate[]): string[] {
  return getPopupEligibleItems(items).map((item) => item.id);
}

export function localEndOfDayUtcIso(now: Date = new Date()): string {
  const end = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    23,
    59,
    59,
    999,
  );
  return end.toISOString();
}
