import {
  getEmojiAvatarUrl,
  isEmojiAvatar,
  isPersonaImageAvatar,
} from "../../persona/personaAvatar";
import {
  PersonaAvatarIcon,
  PersonaAvatarImage,
} from "../../persona/PersonaAvatarIcon";

/** Small round expert avatar built from `avatar` alone (task cards have no full preset). */
export function PersonaAvatarBadge({
  avatar,
  size = 20,
}: {
  avatar?: string | null;
  size?: number;
}) {
  const box = { width: size, height: size };
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-stone-100 dark:bg-stone-800"
      style={box}
      aria-hidden="true"
    >
      {isEmojiAvatar(avatar) ? (
        <img
          src={getEmojiAvatarUrl(avatar)}
          alt=""
          className="size-full object-contain p-0.5"
        />
      ) : isPersonaImageAvatar(avatar) ? (
        <PersonaAvatarImage avatar={avatar} className="size-full object-cover" />
      ) : (
        <PersonaAvatarIcon avatar={avatar} size={Math.round(size * 0.7)} />
      )}
    </span>
  );
}
