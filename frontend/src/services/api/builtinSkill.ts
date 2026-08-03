/**
 * Builtin Skill API - 内置技能管理 (admin)
 *
 * Endpoints under /api/admin/builtin-skills (require manage_builtin_skills).
 * Used by the admin "Builtin Skills" tab to create skills injected into roles
 * (from ZIP or marketplace) and manage their role bindings / active state.
 */

import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import type {
  BuiltinSkill,
  BuiltinSkillListParams,
  BuiltinSkillUpdate,
  BuiltinSkillZipCreated,
  BuiltinSkillZipPreviewSkill,
  BuiltinSkillFromMarketplaceRequest,
  MarketplaceSkillResponse,
} from "../../types";

const BUILTIN_SKILLS_API = `${API_BASE}/api/admin/builtin-skills`;

export function buildBuiltinSkillListUrl(
  params: BuiltinSkillListParams = {},
): string {
  const searchParams = new URLSearchParams();
  if (params.include_inactive !== undefined)
    searchParams.set("include_inactive", String(params.include_inactive));
  if (params.allowed_role) searchParams.set("allowed_role", params.allowed_role);
  if (params.source) searchParams.set("source", params.source);
  if (params.skip !== undefined) searchParams.set("skip", String(params.skip));
  if (params.limit !== undefined)
    searchParams.set("limit", String(params.limit));
  const query = searchParams.toString();
  return `${BUILTIN_SKILLS_API}/${query ? `?${query}` : ""}`;
}

export function buildBuiltinMarketplaceListUrl(params?: {
  skip?: number;
  limit?: number;
}): string {
  const searchParams = new URLSearchParams();
  if (params?.skip !== undefined) searchParams.set("skip", String(params.skip));
  if (params?.limit !== undefined) searchParams.set("limit", String(params.limit));
  const query = searchParams.toString();
  return `${BUILTIN_SKILLS_API}/marketplace${query ? `?${query}` : ""}`;
}

export const builtinSkillApi = {
  /** List builtin skills (admin view, includes file_count). */
  async list(params: BuiltinSkillListParams = {}): Promise<BuiltinSkill[]> {
    return authFetch<BuiltinSkill[]>(buildBuiltinSkillListUrl(params));
  },

  /** List active Marketplace Skills for the Builtin admin source selector. */
  async listMarketplace(params?: {
    skip?: number;
    limit?: number;
  }): Promise<MarketplaceSkillResponse[]> {
    return authFetch<MarketplaceSkillResponse[]>(
      buildBuiltinMarketplaceListUrl(params),
    );
  },

  /** Preview skills in a ZIP file and mark already-existing builtin skills. */
  async previewZip(file: File): Promise<{
    skill_count: number;
    skills: BuiltinSkillZipPreviewSkill[];
  }> {
    const formData = new FormData();
    formData.append("file", file);
    return authFetch(`${BUILTIN_SKILLS_API}/zip/preview`, {
      method: "POST",
      body: formData,
    });
  },

  /** Create builtin skill(s) from a ZIP archive with role bindings. */
  async uploadZip(
    file: File,
    allowedRoles: string[],
  ): Promise<{
    message: string;
    created: BuiltinSkillZipCreated[];
    skill_count: number;
  }> {
    const formData = new FormData();
    formData.append("file", file);
    allowedRoles.forEach((role) => formData.append("allowed_roles", role));
    return authFetch(`${BUILTIN_SKILLS_API}/zip`, {
      method: "POST",
      body: formData,
    });
  },

  /** Create a builtin skill by copying from a marketplace skill. */
  async fromMarketplace(
    data: BuiltinSkillFromMarketplaceRequest,
  ): Promise<BuiltinSkill> {
    return authFetch<BuiltinSkill>(`${BUILTIN_SKILLS_API}/from-marketplace`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  /** Update a builtin skill (description / allowed_roles / is_active). */
  async update(name: string, data: BuiltinSkillUpdate): Promise<BuiltinSkill> {
    return authFetch<BuiltinSkill>(
      `${BUILTIN_SKILLS_API}/${encodeURIComponent(name)}`,
      {
        method: "PATCH",
        body: JSON.stringify(data),
      },
    );
  },

  /** Delete a builtin skill (metadata + files). */
  async delete(name: string): Promise<{ message: string }> {
    return authFetch<{ message: string }>(
      `${BUILTIN_SKILLS_API}/${encodeURIComponent(name)}`,
      {
        method: "DELETE",
      },
    );
  },
};
