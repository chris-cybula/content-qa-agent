// Run page (/runs/[runId])
// Run summary, pages, findings with review buttons, and the agent's fix proposals.

import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import {
  type Finding,
  type FindingStatus,
  getFindings,
  getProposals,
  getRun,
  getRunPages,
  isFindingStatus,
  NotFoundError,
  type PageSummary,
  type Proposal,
  type RunSummary,
} from "@/lib/api";
import { crawlDuration, formatCost, formatDate, formatDuration, shortUrl } from "@/lib/format";

import { FindingCard } from "./finding-card";
import { ProposalCard } from "./proposal-card";

type Filters = {
  pageId?: number;
  status?: FindingStatus;
};

const statusTabs: { status?: FindingStatus; label: string }[] = [
  { label: "All" },
  { status: "open", label: "Open" },
  { status: "approved", label: "Approved" },
  { status: "rejected", label: "Rejected" },
];

function runHref(runId: number, filters: Filters): string {
  const query = new URLSearchParams();
  if (filters.pageId !== undefined) {
    query.set("page", String(filters.pageId));
  }
  if (filters.status !== undefined) {
    query.set("status", filters.status);
  }
  const suffix = query.size > 0 ? `?${query}` : "";
  return `/runs/${runId}${suffix}#findings`;
}

function firstValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function readFilters(query: Record<string, string | string[] | undefined>): Filters {
  const page = Number(firstValue(query.page));
  const status = firstValue(query.status);
  return {
    pageId: Number.isInteger(page) && page > 0 ? page : undefined,
    status: isFindingStatus(status) ? status : undefined,
  };
}

function RunSummaryPanel({ run }: { run: RunSummary }) {
  const stats = [
    {
      label: "Pages",
      value: String(run.page_count),
      detail: run.failed_count > 0 ? `${run.failed_count} failed` : "",
    },
    { label: "Findings", value: String(run.finding_count), detail: `${run.open_count} open` },
    {
      label: "AI cost",
      value: formatCost(run.cost_usd),
      detail: `${run.claude_calls} Claude calls`,
    },
    { label: "Claude time", value: formatDuration(run.ai_duration_ms), detail: "" },
    { label: "Crawl time", value: crawlDuration(run.started_at, run.finished_at), detail: "" },
  ];

  return (
    <section className="run-summary">
      <div className="run-summary__header">
        <div>
          <h1 className="run-summary__title">Run {run.id}</h1>
          <p className="run-summary__meta">
            {run.sitemap_url} · {formatDate(run.started_at)} ·{" "}
            <span className={`status-badge status-badge--${run.status}`}>{run.status}</span>
          </p>
        </div>
        <div className="run-summary__exports">
          <Link className="run-summary__export" href={`/runs/${run.id}/chat`}>
            Chat about this run
          </Link>
          <a className="run-summary__export" href={`/runs/${run.id}/report/csv`} download>
            Download CSV
          </a>
          <a className="run-summary__export" href={`/runs/${run.id}/report/html`} download>
            Download HTML report
          </a>
        </div>
      </div>
      <dl className="run-summary__stats">
        {stats.map((stat) => (
          <div className="run-summary__stat" key={stat.label}>
            <dt className="run-summary__stat-label">{stat.label}</dt>
            <dd className="run-summary__stat-value">{stat.value}</dd>
            {stat.detail && <dd className="run-summary__stat-detail">{stat.detail}</dd>}
          </div>
        ))}
      </dl>
    </section>
  );
}

function PagesTable({
  runId,
  pages,
  filters,
}: {
  runId: number;
  pages: PageSummary[];
  filters: Filters;
}) {
  return (
    <section className="section">
      <div className="section__header">
        <h2 className="section__title">Pages</h2>
        {filters.pageId !== undefined && (
          <Link className="section__link" href={runHref(runId, { status: filters.status })}>
            Show findings for all pages
          </Link>
        )}
      </div>
      <div className="data-table">
        <table className="data-table__table">
          <thead>
            <tr className="data-table__row">
              <th className="data-table__heading">Page</th>
              <th className="data-table__heading data-table__heading--number">Status</th>
              <th className="data-table__heading data-table__heading--number">Words</th>
              <th className="data-table__heading data-table__heading--number">Findings</th>
              <th className="data-table__heading data-table__heading--number">Open</th>
            </tr>
          </thead>
          <tbody>
            {pages.map((page) => (
              <tr
                className={`data-table__row${page.id === filters.pageId ? " data-table__row--selected" : ""}`}
                key={page.id}
              >
                <td className="data-table__cell">
                  <Link
                    className="data-table__link"
                    href={runHref(runId, { pageId: page.id, status: filters.status })}
                  >
                    {shortUrl(page.url)}
                  </Link>
                  <span className="data-table__detail">
                    {page.error ?? page.title ?? "No title"}
                  </span>
                </td>
                <td className="data-table__cell data-table__cell--number">
                  {page.status_code ?? "—"}
                </td>
                <td className="data-table__cell data-table__cell--number">{page.word_count}</td>
                <td className="data-table__cell data-table__cell--number">{page.finding_count}</td>
                <td className="data-table__cell data-table__cell--number">{page.open_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function FindingsList({
  runId,
  findings,
  filters,
  pageUrl,
}: {
  runId: number;
  findings: Finding[];
  filters: Filters;
  pageUrl?: string;
}) {
  return (
    <section className="section" id="findings">
      <div className="section__header">
        <h2 className="section__title">Findings{pageUrl ? ` on ${shortUrl(pageUrl)}` : ""}</h2>
        <nav className="filter-tabs" aria-label="Filter findings by review status">
          {statusTabs.map((tab) => (
            <Link
              className={`filter-tabs__tab${tab.status === filters.status ? " filter-tabs__tab--active" : ""}`}
              href={runHref(runId, { pageId: filters.pageId, status: tab.status })}
              key={tab.label}
              aria-current={tab.status === filters.status ? "page" : undefined}
            >
              {tab.label}
            </Link>
          ))}
        </nav>
      </div>
      {findings.length === 0 ? (
        <p className="section__note">No findings here.</p>
      ) : (
        <div className="section__list">
          {findings.map((finding) => (
            <FindingCard
              finding={finding}
              showPage={filters.pageId === undefined}
              key={finding.id}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function ProposalsList({ runId, proposals }: { runId: number; proposals: Proposal[] }) {
  const pendingCount = proposals.filter((proposal) => proposal.status === "pending").length;

  return (
    <section className="section" id="proposals">
      <div className="section__header">
        <h2 className="section__title">Proposed fixes</h2>
        <Link className="section__link" href={`/runs/${runId}/chat`}>
          Ask for more in the chat
        </Link>
      </div>
      <p className="section__note">
        {pendingCount} waiting for review. The agent proposed these; nothing changes until you
        approve one.
      </p>
      <div className="section__list">
        {proposals.map((proposal) => (
          <ProposalCard proposal={proposal} key={proposal.id} />
        ))}
      </div>
    </section>
  );
}

async function RunView({
  params,
  searchParams,
}: {
  params: Promise<{ runId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { runId } = await params;
  const id = Number(runId);
  if (!Number.isInteger(id) || id <= 0) {
    notFound();
  }
  const filters = readFilters(await searchParams);

  let run: RunSummary;
  let pages: PageSummary[];
  let findings: Finding[];
  let proposals: Proposal[];
  try {
    [run, pages, findings, proposals] = await Promise.all([
      getRun(id),
      getRunPages(id),
      getFindings(id, filters),
      getProposals(id),
    ]);
  } catch (error) {
    if (error instanceof NotFoundError) {
      notFound();
    }
    throw error;
  }

  const selectedPage = pages.find((page) => page.id === filters.pageId);

  return (
    <>
      <RunSummaryPanel run={run} />
      <PagesTable runId={id} pages={pages} filters={filters} />
      {proposals.length > 0 && <ProposalsList runId={id} proposals={proposals} />}
      <FindingsList runId={id} findings={findings} filters={filters} pageUrl={selectedPage?.url} />
    </>
  );
}

export default function RunPage({ params, searchParams }: PageProps<"/runs/[runId]">) {
  return (
    <>
      <Link className="section__link" href="/">
        ← All runs
      </Link>
      <Suspense fallback={<p className="section__note">Loading run…</p>}>
        <RunView params={params} searchParams={searchParams} />
      </Suspense>
    </>
  );
}
