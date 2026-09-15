export type SkillsHubTab = "skills" | "marketplace" | "builtin";

export type WorkspaceHubTab = "expert" | "skills" | "connectors";

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

export function resolveWorkspaceHubTab(
  requestedTab: string | null | undefined,
  canReadSkills: boolean,
  canReadConnectors: boolean,
): WorkspaceHubTab {
  if (requestedTab === "skills" && canReadSkills) return "skills";
  if (requestedTab === "connectors" && canReadConnectors) return "connectors";

  return "expert";
}
