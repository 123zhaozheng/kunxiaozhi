export function quotedSkillNames(skillNames: string[]): string {
  return skillNames.map((name) => `「${name}」`).join("");
}

export const MUST_USE_PREFIX_TEMPLATES = [
  "请必须使用{{names}}技能。",
  "You must use {{names}}.",
  "{{names}}スキルを必ず使用してください。",
  "{{names}} 스킬을 반드시 사용하세요.",
  "Обязательно используйте {{names}}.",
] as const;

export function buildEmphasizedUserMessage(
  content: string,
  skillNames: string[],
  prefixForNames: (names: string) => string,
): string {
  if (skillNames.length === 0) return content;
  const prefix = prefixForNames(quotedSkillNames(skillNames));
  if (!prefix) return content;
  return `${prefix}\n\n${content}`;
}

export interface ParsedEmphasizedUserMessage {
  skillNames: string[];
  visibleContent: string;
}

const QUOTED_SKILL_NAME = /「([^」]+)」/g;

function extractConsecutiveQuotedNames(paragraph: string): string[] {
  const matches: Array<{ name: string; start: number; end: number }> = [];
  for (const match of paragraph.matchAll(new RegExp(QUOTED_SKILL_NAME))) {
    const name = match[1];
    const start = match.index ?? -1;
    if (!name || start < 0) continue;
    matches.push({ name, start, end: start + match[0].length });
  }
  if (matches.length === 0) return [];

  const names = [matches[0].name];
  for (let i = 1; i < matches.length; i++) {
    if (matches[i].start !== matches[i - 1].end) return [];
    names.push(matches[i].name);
  }
  return names;
}

function isMustUsePrefix(paragraph: string, names: string[]): boolean {
  if (names.length === 0) return false;
  const quoted = quotedSkillNames(names);
  return MUST_USE_PREFIX_TEMPLATES.some(
    (template) => template.replace("{{names}}", quoted) === paragraph,
  );
}

export function parseEmphasizedUserMessage(
  content: string,
): ParsedEmphasizedUserMessage {
  const splitIndex = content.indexOf("\n\n");
  if (splitIndex < 0) {
    return { skillNames: [], visibleContent: content };
  }

  const firstParagraph = content.slice(0, splitIndex);
  const remainder = content.slice(splitIndex + 2);
  const skillNames = extractConsecutiveQuotedNames(firstParagraph);
  if (!isMustUsePrefix(firstParagraph, skillNames)) {
    return { skillNames: [], visibleContent: content };
  }
  return { skillNames, visibleContent: remainder };
}
