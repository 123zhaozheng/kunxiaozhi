import { Briefcase, MessageCircle, Users, Sparkles } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { isKnownAgentMode, type AgentModeId } from "./agentModePresentation";

/**
 * Mode icons are deliberately NOT routed through DynamicIcon/AgentIcon: those
 * resolve every plain ASCII name to a 3D emoji asset, which reads as heavy
 * clip-art next to the flat line icons used by the sidebar. Mode switchers
 * want the same 1.5px stroked Lucide set as the nav rail.
 */
const MODE_ICONS: Record<AgentModeId, LucideIcon> = {
  search: Briefcase,
  fast: MessageCircle,
  team: Users,
};

export function AgentModeIcon({
  agentId,
  size = 16,
  className,
}: {
  agentId: string;
  size?: number;
  className?: string;
}) {
  const Icon = isKnownAgentMode(agentId) ? MODE_ICONS[agentId] : Sparkles;
  return (
    <Icon size={size} strokeWidth={1.75} className={className} aria-hidden />
  );
}
