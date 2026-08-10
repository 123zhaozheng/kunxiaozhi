/**
 * Build the approval response payload for the SOP card confirm / replan
 * buttons. Confirm sends an empty object; replan sends `{ feedback }`
 * (trimmed) when a message was provided.
 */
export function buildSopRespondResponse(
  approved: boolean,
  feedback: string,
): Record<string, unknown> {
  if (approved) return {};
  const trimmed = feedback.trim();
  return trimmed ? { feedback: trimmed } : {};
}
