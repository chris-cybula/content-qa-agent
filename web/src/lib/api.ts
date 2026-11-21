// API client
// Types and fetch helpers for the FastAPI backend (API_URL, default 127.0.0.1:8000).

export const apiUrl = process.env.API_URL ?? "http://127.0.0.1:8000";

export type FindingStatus = "open" | "approved" | "rejected";

export const findingStatuses: FindingStatus[] = ["open", "approved", "rejected"];

export type Severity = "high" | "medium" | "low";

export type RunSummary = {
  id: number;
  sitemap_url: string;
  status: string;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  page_count: number;
  failed_count: number;
  finding_count: number;
  open_count: number;
  claude_calls: number;
  cost_usd: string;
  ai_duration_ms: number;
};

export type PageSummary = {
  id: number;
  url: string;
  status_code: number | null;
  error: string | null;
  title: string | null;
  word_count: number;
  finding_count: number;
  open_count: number;
};

export type Finding = {
  id: number;
  page_id: number;
  page_url: string;
  rule: string;
  severity: Severity;
  element: string;
  evidence: string;
  suggested_fix: string;
  status: FindingStatus;
};

export type ProposalStatus = "pending" | "approved" | "rejected";

export const proposalStatuses: ProposalStatus[] = ["pending", "approved", "rejected"];

export type Proposal = {
  id: number;
  run_id: number;
  finding_id: number;
  page_id: number;
  page_url: string;
  rule: string;
  element: string;
  evidence: string;
  suggested_fix: string;
  new_text: string;
  reason: string;
  status: ProposalStatus;
  created_at: string;
};

export type ChatMessage = {
  role: "user" | "assistant";
  text: string;
};

export type ChatReply = {
  run_id: number;
  answer: string;
  stopped: string | null;
  rounds: number;
  tools_used: string[];
  proposals: Proposal[];
};

export class NotFoundError extends Error {}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiUrl}${path}`, { cache: "no-store" });

  if (response.status === 404) {
    throw new NotFoundError(path);
  }
  if (!response.ok) {
    throw new Error(`API answered ${response.status} for ${path}`);
  }
  return response.json();
}

export function getRuns(): Promise<RunSummary[]> {
  return getJson("/runs");
}

export function getRun(runId: number): Promise<RunSummary> {
  return getJson(`/runs/${runId}`);
}

export function getRunPages(runId: number): Promise<PageSummary[]> {
  return getJson(`/runs/${runId}/pages`);
}

export function getFindings(
  runId: number,
  filters: { pageId?: number; status?: FindingStatus },
): Promise<Finding[]> {
  const query = new URLSearchParams();
  if (filters.pageId !== undefined) {
    query.set("page_id", String(filters.pageId));
  }
  if (filters.status !== undefined) {
    query.set("status", filters.status);
  }
  const suffix = query.size > 0 ? `?${query}` : "";
  return getJson(`/runs/${runId}/findings${suffix}`);
}

export async function setFindingStatus(findingId: number, status: FindingStatus): Promise<void> {
  const response = await fetch(`${apiUrl}/findings/${findingId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ status }),
  });

  if (!response.ok) {
    throw new Error(`API answered ${response.status} when reviewing finding ${findingId}`);
  }
}

export function isFindingStatus(value: unknown): value is FindingStatus {
  return typeof value === "string" && findingStatuses.includes(value as FindingStatus);
}

export function getProposals(runId: number): Promise<Proposal[]> {
  return getJson(`/runs/${runId}/proposals`);
}

export async function setProposalStatus(proposalId: number, status: ProposalStatus): Promise<void> {
  const response = await fetch(`${apiUrl}/proposals/${proposalId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ status }),
  });

  if (!response.ok) {
    throw new Error(`API answered ${response.status} when reviewing proposal ${proposalId}`);
  }
}

export function isProposalStatus(value: unknown): value is ProposalStatus {
  return typeof value === "string" && proposalStatuses.includes(value as ProposalStatus);
}

export async function sendChat(runId: number, messages: ChatMessage[]): Promise<ChatReply> {
  const response = await fetch(`${apiUrl}/runs/${runId}/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messages }),
    cache: "no-store",
  });

  if (response.status === 404) {
    throw new NotFoundError(`/runs/${runId}/chat`);
  }
  if (!response.ok) {
    throw new Error(`API answered ${response.status} for the chat`);
  }
  return response.json();
}
