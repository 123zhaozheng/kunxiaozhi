import { useState, useCallback } from "react";
import {
  Code2,
  Database,
  GraduationCap,
  Package,
  PenTool,
  Shield,
  Sparkles,
  Zap,
  type LucideIcon,
} from "lucide-react";
import {
  getPersonaAvatarIcon,
  getEmojiAvatarUrl,
  isPersonaImageAvatar,
  type PersonaAvatarIconKey,
} from "./personaAvatar";
import { getCategoryIcon } from "../panels/MarketplacePanel/constants";
import { getFullUrl } from "../../services/api";

const DEFAULT_AVATAR_EMOJI = "🤖";
const DEFAULT_AVATAR_SRC = getEmojiAvatarUrl(DEFAULT_AVATAR_EMOJI);

const ICONS: Record<PersonaAvatarIconKey, LucideIcon> = {
  sparkles: Sparkles,
  academic: GraduationCap,
  coding: Code2,
  writing: PenTool,
  security: Shield,
  data: Database,
  productivity: Zap,
  general: Package,
};

export function PersonaAvatarIcon({
  avatar,
  primaryTag,
  size = 16,
  className = "",
}: {
  avatar?: string | null;
  primaryTag?: string;
  size?: number;
  className?: string;
}) {
  const builtIn = getPersonaAvatarIcon(avatar);
  if (builtIn) {
    const Icon = ICONS[builtIn.key];
    return (
      <Icon
        size={size}
        className={className}
        style={{ color: builtIn.color }}
      />
    );
  }

  const CategoryIcon = primaryTag ? getCategoryIcon(primaryTag) : null;
  if (CategoryIcon) {
    return <CategoryIcon size={size} className={className} />;
  }

  return (
    <img
      src={DEFAULT_AVATAR_SRC}
      alt=""
      width={size}
      height={size}
      className={className}
      style={{ objectFit: "contain" }}
    />
  );
}

export function PersonaAvatarImage({
  avatar,
  alt = "",
  className = "",
  onLoad,
  onError,
}: {
  avatar?: string | null;
  alt?: string;
  className?: string;
  onLoad?: React.ReactEventHandler<HTMLImageElement>;
  onError?: React.ReactEventHandler<HTMLImageElement>;
}) {
  const [loaded, setLoaded] = useState(false);

  const handleLoad = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      setLoaded(true);
      onLoad?.(e);
    },
    [onLoad],
  );

  const handleError = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      setLoaded(true);
      onError?.(e);
    },
    [onError],
  );

  if (!isPersonaImageAvatar(avatar)) return null;
  const resolvedAvatar = getFullUrl(avatar) ?? avatar;
  return (
    <img
      src={resolvedAvatar}
      alt={alt}
      className={className}
      onLoad={handleLoad}
      onError={handleError}
      style={loaded ? {} : { opacity: 0 }}
    />
  );
}
