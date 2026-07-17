import { useEffect, useState } from "react";
import { getEmojiAvatarSrcCandidates } from "../persona/personaAvatar";

/**
 * Same-origin anim webp for emoji icons (replaces FluentEmoji CDN `type="3d"`).
 * Tries FE0F filename variants on error, then falls back to the unicode glyph.
 */
export function LocalFluentEmoji({
  emoji,
  size,
  className,
}: {
  emoji: string;
  size?: number;
  className?: string;
}) {
  const candidates = getEmojiAvatarSrcCandidates(emoji);
  const [index, setIndex] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setIndex(0);
    setFailed(false);
  }, [emoji]);

  if (failed || index >= candidates.length) {
    return (
      <span
        className={[
          "inline-flex items-center justify-center overflow-hidden",
          className,
        ]
          .filter(Boolean)
          .join(" ")}
        style={{ width: size, height: size, fontSize: size, lineHeight: 1 }}
        aria-hidden
      >
        {emoji}
      </span>
    );
  }

  return (
    <span
      className={[
        "inline-flex items-center justify-center overflow-hidden",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      style={{ width: size, height: size, fontSize: size, lineHeight: 1 }}
    >
      <img
        src={candidates[index]}
        alt=""
        width={size}
        height={size}
        draggable={false}
        style={{
          width: size,
          height: size,
          objectFit: "contain",
          display: "block",
        }}
        onError={() => {
          if (index + 1 < candidates.length) setIndex((i) => i + 1);
          else setFailed(true);
        }}
      />
    </span>
  );
}
