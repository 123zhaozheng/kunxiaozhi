import { skillMatchesQuery } from "../../utils/skillFilters";

export interface ChatInputSlashCommand {
  id: "goal";
  command: "/goal";
  labelKey: string;
  fallbackLabel: string;
}

export interface SlashSkillCandidate {
  name: string;
  description: string;
  tags: string[];
  enabled: boolean;
}

export type ChatInputSlashListItem =
  | { kind: "command"; id: string; command: ChatInputSlashCommand }
  | { kind: "skill"; id: string; name: string; description: string };

export const CHAT_INPUT_SLASH_COMMANDS: ChatInputSlashCommand[] = [
  {
    id: "goal",
    command: "/goal",
    labelKey: "chat.goal.command",
    fallbackLabel: "Goal",
  },
];

export function getSlashCommandQuery(
  input: string,
  cursorPosition: number,
): string | null {
  const beforeCursor = input.slice(0, cursorPosition);
  if (!beforeCursor.startsWith("/")) return null;
  if (beforeCursor.includes(" ") || beforeCursor.includes("\n")) return null;
  return beforeCursor.slice(1).toLowerCase();
}

export function getMatchingSlashCommands(
  input: string,
  cursorPosition: number,
): ChatInputSlashCommand[] {
  const query = getSlashCommandQuery(input, cursorPosition);
  if (query === null) return [];
  return CHAT_INPUT_SLASH_COMMANDS.filter((item) =>
    item.command.slice(1).startsWith(query),
  );
}

export function getMatchingSlashItems(
  input: string,
  cursorPosition: number,
  skills: SlashSkillCandidate[] = [],
): ChatInputSlashListItem[] {
  const query = getSlashCommandQuery(input, cursorPosition);
  if (query === null) return [];

  const commands: ChatInputSlashListItem[] = getMatchingSlashCommands(
    input,
    cursorPosition,
  ).map((command) => ({
    kind: "command",
    id: `command:${command.id}`,
    command,
  }));

  const skillItems: ChatInputSlashListItem[] = skills
    .filter((skill) => skill.enabled && skillMatchesQuery(skill, query))
    .map((skill) => ({
      kind: "skill",
      id: `skill:${skill.name}`,
      name: skill.name,
      description: skill.description,
    }));

  return [...commands, ...skillItems];
}

export function applySlashCommandSelection(
  input: string,
  cursorPosition: number,
  command: ChatInputSlashCommand,
): { input: string; cursorPosition: number } {
  const beforeCursor = input.slice(0, cursorPosition);
  const afterCursor = input.slice(cursorPosition);
  const commandStart = beforeCursor.startsWith("/") ? 0 : cursorPosition;
  const nextInput = `${input.slice(0, commandStart)}${
    command.command
  } ${afterCursor}`;
  const nextCursorPosition = commandStart + command.command.length + 1;
  return { input: nextInput, cursorPosition: nextCursorPosition };
}

export function stripSlashCommandQuery(
  input: string,
  cursorPosition: number,
): { input: string; cursorPosition: number } {
  if (getSlashCommandQuery(input, cursorPosition) === null) {
    return { input, cursorPosition };
  }
  const afterCursor = input.slice(cursorPosition);
  return { input: afterCursor, cursorPosition: 0 };
}
