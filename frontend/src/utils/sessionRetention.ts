/**
 * Whether a session's resumable AI context was reclaimed by checkpoint retention.
 *
 * The stamp is only trusted when it is at least as recent as the session's last
 * activity: a later turn rebuilds context, which makes an older stamp stale.
 */
export function isCheckpointsCleaned(
  cleanedAt: unknown,
  updatedAt?: string | null,
): boolean {
  if (typeof cleanedAt !== "string" || cleanedAt.length === 0) return false;

  const cleanedTime = Date.parse(cleanedAt);
  if (Number.isNaN(cleanedTime)) return false;
  if (!updatedAt) return true;

  const updatedTime = Date.parse(updatedAt);
  if (Number.isNaN(updatedTime)) return true;
  return cleanedTime >= updatedTime;
}
