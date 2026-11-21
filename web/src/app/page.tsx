// Runs page (/)
// Lists every run with pages, findings, cost and time.

import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { getRuns, type RunSummary } from "@/lib/api";
import { crawlDuration, formatCost, formatDate, formatDuration } from "@/lib/format";

function RunRow({ run }: { run: RunSummary }) {
  const host = new URL(run.sitemap_url).host;

  return (
    <tr className="data-table__row">
      <td className="data-table__cell">
        <Link className="data-table__link" href={`/runs/${run.id}`}>
          Run {run.id}
        </Link>
        <span className="data-table__detail">{host}</span>
      </td>
      <td className="data-table__cell">{formatDate(run.started_at)}</td>
      <td className="data-table__cell">
        <span className={`status-badge status-badge--${run.status}`}>{run.status}</span>
      </td>
      <td className="data-table__cell data-table__cell--number">
        {run.page_count}
        {run.failed_count > 0 && (
          <span className="data-table__detail">{run.failed_count} failed</span>
        )}
      </td>
      <td className="data-table__cell data-table__cell--number">
        {run.finding_count}
        {run.open_count > 0 && <span className="data-table__detail">{run.open_count} open</span>}
      </td>
      <td className="data-table__cell data-table__cell--number">
        {formatCost(run.cost_usd)}
        {run.claude_calls > 0 && (
          <span className="data-table__detail">{run.claude_calls} calls</span>
        )}
      </td>
      <td className="data-table__cell data-table__cell--number">
        {formatDuration(run.ai_duration_ms)}
      </td>
      <td className="data-table__cell data-table__cell--number">
        {crawlDuration(run.started_at, run.finished_at)}
      </td>
    </tr>
  );
}

async function RunsTable() {
  await connection();
  let runs: RunSummary[];

  try {
    runs = await getRuns();
  } catch {
    return (
      <p className="section__note">
        The API isn&apos;t reachable, so no runs can be shown. Start it and reload.
      </p>
    );
  }

  if (runs.length === 0) {
    return (
      <p className="section__note">No runs yet. Start one with POST /crawl in the API docs.</p>
    );
  }

  return (
    <div className="data-table">
      <table className="data-table__table">
        <thead>
          <tr className="data-table__row">
            <th className="data-table__heading">Run</th>
            <th className="data-table__heading">Started</th>
            <th className="data-table__heading">Status</th>
            <th className="data-table__heading data-table__heading--number">Pages</th>
            <th className="data-table__heading data-table__heading--number">Findings</th>
            <th className="data-table__heading data-table__heading--number">AI cost</th>
            <th className="data-table__heading data-table__heading--number">Claude time</th>
            <th className="data-table__heading data-table__heading--number">Crawl time</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <RunRow key={run.id} run={run} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Home() {
  return (
    <section className="section">
      <div className="section__header">
        <h1 className="section__title">Runs</h1>
        <p className="section__note">The 50 most recent crawls, newest first.</p>
      </div>
      <Suspense fallback={<p className="section__note">Loading runs…</p>}>
        <RunsTable />
      </Suspense>
    </section>
  );
}
