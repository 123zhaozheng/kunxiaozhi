import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const chatInput = readFileSync(join(import.meta.dirname, "../ChatInput.tsx"), "utf8");
const capacityDialog = readFileSync(
  join(import.meta.dirname, "../SandboxCapacityDialog.tsx"),
  "utf8",
);

test("capacity feedback uses a dedicated polished dialog with separate help", () => {
  assert.match(chatInput, /SandboxCapacityDialog/);
  assert.match(chatInput, /chat\.sandboxCapacityMessage/);
  assert.match(
    chatInput,
    /现在有点太火热啦，沙盒资源暂满，请稍后重试。也可以先切换到 Fast 模式继续聊。/,
  );
  assert.match(chatInput, /沙盒资源暂满，请稍后重试/);
  assert.match(chatInput, /太火热了/);
  assert.match(chatInput, /chat\.sandboxCapacityHelp/);
  assert.match(chatInput, /如需协助，可反馈数据资产部赵正通/);
  assert.match(chatInput, /<SandboxCapacityDialog/);
  assert.doesNotMatch(capacityDialog, /\bFlame\b/);
  assert.match(capacityDialog, /HelpCircle/);
  assert.match(capacityDialog, /sandboxCapacityHelpLabel/);
  assert.match(capacityDialog, /aria-expanded=\{helpOpen\}/);
  assert.match(capacityDialog, /role="dialog"/);
  assert.match(capacityDialog, /aria-modal="true"/);
  assert.match(capacityDialog, /backdrop-blur/);
});

test("capacity rejection restores the draft and opens feedback before clearing", () => {
  const handler = chatInput.match(/const handleSubmit[\s\S]*?const handleKeyDown/);
  assert.ok(handler);
  assert.match(handler[0], /isSandboxCapacityError/);
  assert.match(handler[0], /setInput\(draft\)/);
  assert.match(handler[0], /setAttachments\(draftAttachments\)/);
  assert.match(handler[0], /setCapacityModalOpen\(true\)/);
  assert.match(handler[0], /const draftAttachments = \[\.\.\.attachments\]/);
  assert.match(handler[0], /await onSend\([\s\S]*?handleAccepted/);
  assert.ok(handler[0].indexOf("setInput(draft)") < handler[0].indexOf("setCapacityModalOpen(true)"));
  assert.doesNotMatch(handler[0], /toast\.(error|success).*sandbox/);
});

test("ChatView returns onSend promise so capacity rejections reach ChatInput", () => {
  const chatView = readFileSync(
    join(import.meta.dirname, "../../layout/AppContent/ChatView.tsx"),
    "utf8",
  );
  assert.match(
    chatView,
    /return onSendMessage\(content, sendAttachments, onAccepted\)/,
  );
});

test("sendMessage paints optimistic messages only after submitChat succeeds", () => {
  const useAgent = readFileSync(
    join(import.meta.dirname, "../../../hooks/useAgent.ts"),
    "utf8",
  );
  const submitAt = useAgent.indexOf("sessionApi.submitChat");
  const paintAt = useAgent.indexOf(
    "assistantMessageId: newRunId || undefined",
  );
  assert.ok(submitAt > 0);
  assert.ok(paintAt > submitAt);
  const acceptedAt = useAgent.indexOf("onAccepted?.()");
  assert.ok(acceptedAt > submitAt);
  assert.ok(acceptedAt < paintAt);
  assert.match(useAgent, /Keep the welcome ChatInput mounted while admission is pending/);
  assert.match(useAgent, /const previousMessages = messagesRef\.current/);
  assert.match(useAgent, /setMessages\(previousMessages\)/);
  assert.doesNotMatch(useAgent, /sandboxCapacityToast/);
});
