// Finding card
// One finding with its evidence, fix and approve or reject buttons.

import { reviewFinding } from "@/app/actions";
import type { Finding, FindingStatus } from "@/lib/api";
import { shortUrl } from "@/lib/format";

const statusLabels: Record<FindingStatus, string> = {
  open: "Open",
  approved: "Approved",
  rejected: "Rejected",
};

function ReviewButton({ status, label }: { status: FindingStatus; label: string }) {
  return (
    <button
      className={`finding-card__button finding-card__button--${status}`}
      type="submit"
      name="status"
      value={status}
    >
      {label}
    </button>
  );
}

export function FindingCard({ finding, showPage }: { finding: Finding; showPage: boolean }) {
  return (
    <article className={`finding-card finding-card--${finding.status}`}>
      <header className="finding-card__header">
        <span className={`finding-card__severity finding-card__severity--${finding.severity}`}>
          {finding.severity}
        </span>
        <h3 className="finding-card__rule">{finding.rule}</h3>
        <span className={`status-badge status-badge--${finding.status}`}>
          {statusLabels[finding.status]}
        </span>
      </header>
      <dl className="finding-card__details">
        {showPage && (
          <>
            <dt className="finding-card__label">Page</dt>
            <dd className="finding-card__value">{shortUrl(finding.page_url)}</dd>
          </>
        )}
        <dt className="finding-card__label">Element</dt>
        <dd className="finding-card__value">{finding.element}</dd>
        <dt className="finding-card__label">Evidence</dt>
        <dd className="finding-card__value">{finding.evidence}</dd>
        <dt className="finding-card__label">Suggested fix</dt>
        <dd className="finding-card__value">{finding.suggested_fix}</dd>
      </dl>
      <form className="finding-card__actions" action={reviewFinding}>
        <input type="hidden" name="findingId" value={finding.id} />
        {finding.status !== "approved" && <ReviewButton status="approved" label="Approve" />}
        {finding.status !== "rejected" && <ReviewButton status="rejected" label="Reject" />}
        {finding.status !== "open" && <ReviewButton status="open" label="Undo review" />}
      </form>
    </article>
  );
}
