export type PersonaAvatarIconKey =
  | "sparkles"
  | "academic"
  | "coding"
  | "writing"
  | "security"
  | "data"
  | "productivity"
  | "general";

export interface PersonaAvatarIconMeta {
  key: PersonaAvatarIconKey;
  label: string;
  color: string;
  bg: string;
}

export const PERSONA_AVATAR_ICON_PREFIX = "icon:";

const PERSONA_AVATAR_ICONS: PersonaAvatarIconMeta[] = [
  { key: "sparkles", label: "Sparkles", color: "#6366f1", bg: "#eef2ff" },
  { key: "academic", label: "Academic", color: "#0891b2", bg: "#ecfeff" },
  { key: "coding", label: "Coding", color: "#16a34a", bg: "#f0fdf4" },
  { key: "writing", label: "Writing", color: "#c026d3", bg: "#fdf4ff" },
  { key: "security", label: "Security", color: "#dc2626", bg: "#fef2f2" },
  { key: "data", label: "Data", color: "#ea580c", bg: "#fff7ed" },
  {
    key: "productivity",
    label: "Productivity",
    color: "#ca8a04",
    bg: "#fefce8",
  },
  { key: "general", label: "General", color: "#4f46e5", bg: "#eef2ff" },
];

export function getPersonaAvatarIcons(): PersonaAvatarIconMeta[] {
  return PERSONA_AVATAR_ICONS;
}

export function getPersonaAvatarIconValue(key: PersonaAvatarIconKey): string {
  return `${PERSONA_AVATAR_ICON_PREFIX}${key}`;
}

export function getPersonaAvatarIcon(
  avatar: string | null | undefined,
): PersonaAvatarIconMeta | null {
  if (!avatar?.startsWith(PERSONA_AVATAR_ICON_PREFIX)) return null;
  const key = avatar.slice(
    PERSONA_AVATAR_ICON_PREFIX.length,
  ) as PersonaAvatarIconKey;
  return PERSONA_AVATAR_ICONS.find((icon) => icon.key === key) ?? null;
}

export function isPersonaImageAvatar(
  avatar: string | null | undefined,
): avatar is string {
  return !!avatar && !avatar.startsWith(PERSONA_AVATAR_ICON_PREFIX);
}

const EMOJI_RE = /\p{Emoji_Presentation}|\p{Extended_Pictographic}/u;

export function isEmojiAvatar(
  avatar: string | null | undefined,
): avatar is string {
  if (
    !avatar ||
    avatar.startsWith(PERSONA_AVATAR_ICON_PREFIX) ||
    avatar.startsWith("/") ||
    avatar.startsWith("http")
  )
    return false;
  return EMOJI_RE.test(avatar) && avatar.length <= 8;
}

/**
 * Convert an emoji to its hyphen-joined lowercase hex codepoints, matching
 * @lobehub/fluent-emoji's internal emojiToUnicode (Array.from iterates by code
 * point, so astral/flag emoji compose correctly).
 */
export function emojiToCodepoints(emoji: string): string {
  return Array.from(emoji)
    .map((ch) => ch.codePointAt(0)!.toString(16))
    .join("-");
}

/**
 * Local anim filenames sometimes include or omit FE0F. Prefer exact codepoints,
 * then FE0F variants, so missing allowlist/file-name mismatches degrade less.
 */
export function getEmojiAvatarSrcCandidates(emoji: string): string[] {
  const base = emojiToCodepoints(emoji);
  const candidates = [base];
  if (!base.endsWith("-fe0f")) candidates.push(`${base}-fe0f`);
  else candidates.push(base.replace(/-fe0f$/, ""));
  const stripped = base
    .split("-")
    .filter((p) => p !== "fe0f")
    .join("-");
  if (stripped && stripped !== base) candidates.push(stripped);
  return [...new Set(candidates)].map((cp) => `/emoji-assets/${cp}.webp`);
}

export function getEmojiAvatarUrl(emoji: string): string {
  return getEmojiAvatarSrcCandidates(emoji)[0];
}
