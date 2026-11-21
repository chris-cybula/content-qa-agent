// Server actions
// Review findings and proposals, and send chat messages to the API.

"use server";

import { refresh } from "next/cache";

import {
  type ChatMessage,
  type ChatReply,
  isFindingStatus,
  isProposalStatus,
  sendChat,
  setFindingStatus,
  setProposalStatus,
} from "@/lib/api";
import { type ChatState, maxChatMessages, maxQuestionLength } from "@/lib/chat";

export async function reviewFinding(formData: FormData): Promise<void> {
  const findingId = Number(formData.get("findingId"));
  const status = formData.get("status");

  if (!Number.isInteger(findingId) || findingId <= 0 || !isFindingStatus(status)) {
    throw new Error("Invalid review");
  }

  await setFindingStatus(findingId, status);
  refresh();
}

export async function reviewProposal(formData: FormData): Promise<void> {
  const proposalId = Number(formData.get("proposalId"));
  const status = formData.get("status");

  if (!Number.isInteger(proposalId) || proposalId <= 0 || !isProposalStatus(status)) {
    throw new Error("Invalid review");
  }

  await setProposalStatus(proposalId, status);
  refresh();
}

export async function sendChatMessage(state: ChatState, formData: FormData): Promise<ChatState> {
  const runId = Number(formData.get("runId"));
  const question = String(formData.get("question") ?? "").trim();

  if (!Number.isInteger(runId) || runId <= 0) {
    throw new Error("Invalid run");
  }
  if (question === "") {
    return { ...state, notice: "Type a question first." };
  }
  if (question.length > maxQuestionLength) {
    return { ...state, notice: `Keep your question under ${maxQuestionLength} characters.` };
  }
  if (state.messages.length + 1 > maxChatMessages) {
    return { ...state, notice: "This chat is full. Reload the page to start a new one." };
  }

  const messages: ChatMessage[] = [...state.messages, { role: "user", text: question }];

  let reply: ChatReply;
  try {
    reply = await sendChat(runId, messages);
  } catch (error) {
    return { ...state, notice: error instanceof Error ? error.message : "The chat failed." };
  }

  const proposals = [...state.proposals, ...reply.proposals];
  if (reply.stopped !== null) {
    return {
      messages: state.messages,
      proposals,
      notice: `Stopped before answering “${question}”: ${reply.stopped}`,
    };
  }
  return {
    messages: [...messages, { role: "assistant", text: reply.answer }],
    proposals,
    notice: null,
  };
}
