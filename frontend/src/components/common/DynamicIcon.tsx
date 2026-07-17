import { LocalFluentEmoji } from "./LocalFluentEmoji";

// Legacy default icons → mapped to 💬 local emoji asset
const LEGACY_DEFAULT_ICONS = new Set(["MessageCircle", "Bot", "📁"]);

// Dynamic icon renderer - emoji icons use local /emoji-assets/ anim webp
export function DynamicIcon({
  name,
  size,
  className,
}: {
  name?: string;
  size?: number;
  className?: string;
}) {
  if (!name || LEGACY_DEFAULT_ICONS.has(name)) {
    return <LocalFluentEmoji emoji="💬" size={size} className={className} />;
  }
  // Check if it's an emoji (non-ASCII character, or no ASCII letters)
  const isEmoji = !/^[a-zA-Z]+$/.test(name);
  if (isEmoji) {
    return <LocalFluentEmoji emoji={name} size={size} className={className} />;
  }
  // Unrecognized ASCII names fall back to 💬
  return <LocalFluentEmoji emoji="💬" size={size} className={className} />;
}
