import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { getTeamRouteRequest } from "../teamRouteState";

const chatAppContentSource = readFileSync(
  new URL("../ChatAppContent.tsx", import.meta.url),
  "utf8",
);

test("reads team use requests from chat query params", () => {
  assert.deepEqual(
    getTeamRouteRequest(new URLSearchParams("agent=team&team=team-123"), null),
    {
      agentId: "team",
      teamId: "team-123",
    },
  );
});

test("reads team use requests from route state", () => {
  assert.deepEqual(
    getTeamRouteRequest(new URLSearchParams(), {
      agentId: "team",
      teamId: "team-456",
    }),
    {
      agentId: "team",
      teamId: "team-456",
    },
  );
});

test("ignores incomplete team use requests", () => {
  assert.equal(
    getTeamRouteRequest(new URLSearchParams("agent=team"), null),
    null,
  );
  assert.equal(
    getTeamRouteRequest(new URLSearchParams("team=team-123"), null),
    null,
  );
});

test("chat app applies team route requests to agent and team selection", () => {
  assert.match(
    chatAppContentSource,
    /getTeamRouteRequest\(searchParams,\s*location\.state\)/,
  );
  assert.match(chatAppContentSource, /switchAgentRaw\(teamRequest\.agentId\)/);
  assert.match(chatAppContentSource, /selectTeam\(teamRequest\.teamId\)/);
});

test("chat app switches to preferred persona agent when using a persona", () => {
  assert.match(chatAppContentSource, /resolvePersonaAgentId/);
  assert.match(
    chatAppContentSource,
    /const switchToPersonaAgentMode = useCallback/,
  );
  assert.match(
    chatAppContentSource,
    /resolvePersonaAgentId\(preferredAgentId,\s*currentAgent\)/,
  );
  assert.match(
    chatAppContentSource,
    /if \(nextAgentId !== "team"\) \{\s*selectTeam\(null\);\s*\}/,
  );
  assert.match(
    chatAppContentSource,
    /switchToPersonaAgentMode\([\s\S]*preferred_agent_id[\s\S]*\)[\s\S]*setPersonaPreset\(preset\.id, snapshot\)/,
  );
});
