export interface PasswordRequirementsAnchor {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface PasswordRequirementsViewport {
  width: number;
  height: number;
}

export interface PasswordRequirementsPosition {
  left: number;
  top: number;
  width: number;
  placement: "above" | "below";
}

const DEFAULT_PANEL_WIDTH = 320;
const DEFAULT_PANEL_HEIGHT = 260;
const VIEWPORT_MARGIN = 12;
const ANCHOR_GAP = 8;

export function calculatePasswordRequirementsPosition(
  anchor: PasswordRequirementsAnchor,
  viewport: PasswordRequirementsViewport,
  panel = { width: DEFAULT_PANEL_WIDTH, height: DEFAULT_PANEL_HEIGHT },
): PasswordRequirementsPosition {
  const width = Math.min(
    panel.width,
    Math.max(1, viewport.width - VIEWPORT_MARGIN * 2),
  );
  const maxLeft = Math.max(VIEWPORT_MARGIN, viewport.width - width - VIEWPORT_MARGIN);
  const left = Math.min(Math.max(anchor.left, VIEWPORT_MARGIN), maxLeft);
  const spaceBelow = viewport.height - anchor.bottom - VIEWPORT_MARGIN;
  const spaceAbove = anchor.top - VIEWPORT_MARGIN;
  const placement =
    spaceBelow < panel.height + ANCHOR_GAP &&
    spaceAbove >= panel.height + ANCHOR_GAP
      ? "above"
      : "below";
  const rawTop =
    placement === "above"
      ? anchor.top - panel.height - ANCHOR_GAP
      : anchor.bottom + ANCHOR_GAP;
  const maxTop = Math.max(VIEWPORT_MARGIN, viewport.height - panel.height - VIEWPORT_MARGIN);

  return {
    left,
    top: Math.min(Math.max(rawTop, VIEWPORT_MARGIN), maxTop),
    width,
    placement,
  };
}
