import type { Project } from "../../../types";

export function isSidebarProject(project: Project): boolean {
  return project.type === "custom";
}

export function isWeComChannelProject(project: Project): boolean {
  return project.type === "channel";
}
