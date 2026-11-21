// Chat state
// Shared chat types, the empty state and the question length limit.

import type { ChatMessage, Proposal } from "@/lib/api";

export type ChatState = {
  messages: ChatMessage[];
  proposals: Proposal[];
  notice: string | null;
};

export const emptyChat: ChatState = {
  messages: [],
  proposals: [],
  notice: null,
};

export const maxQuestionLength = 4000;

export const maxChatMessages = 20;
