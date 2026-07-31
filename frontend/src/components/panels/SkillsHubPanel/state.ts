export type SkillsHubTab = "skills" | "marketplace" | "builtin";

export function resolveSkillsHubTab(
  requestedTab: SkillsHubTab | undefined,
  canReadSkills: boolean,
  canReadMarketplace: boolean,
): SkillsHubTab | null {
  if (canReadSkills && canReadMarketplace) {
    return requestedTab === "marketplace" ? "marketplace" : "skills";
  }

  if (canReadSkills) {
    return "skills";
  }

  if (canReadMarketplace) {
    return "marketplace";
  }

  return null;
}
