// Proposal card
// One agent fix proposal with approve or reject buttons.

import { reviewProposal } from "@/app/actions";
import type { Proposal, ProposalStatus } from "@/lib/api";
import { shortUrl } from "@/lib/format";

const statusLabels: Record<ProposalStatus, string> = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
};

function ReviewButton({ status, label }: { status: ProposalStatus; label: string }) {
  return (
    <button
      className={`proposal-card__button proposal-card__button--${status}`}
      type="submit"
      name="status"
      value={status}
    >
      {label}
    </button>
  );
}

export function ProposalCard({ proposal }: { proposal: Proposal }) {
  return (
    <article className={`proposal-card proposal-card--${proposal.status}`}>
      <header className="proposal-card__header">
        <h3 className="proposal-card__rule">{proposal.rule}</h3>
        <span className="proposal-card__page">{shortUrl(proposal.page_url)}</span>
        <span className={`status-badge status-badge--${proposal.status}`}>
          {statusLabels[proposal.status]}
        </span>
      </header>
      <dl className="proposal-card__details">
        <dt className="proposal-card__label">Element</dt>
        <dd className="proposal-card__value">{proposal.element}</dd>
        <dt className="proposal-card__label">Problem</dt>
        <dd className="proposal-card__value">{proposal.evidence}</dd>
        <dt className="proposal-card__label">Proposed text</dt>
        <dd className="proposal-card__value proposal-card__value--new">{proposal.new_text}</dd>
        <dt className="proposal-card__label">Why</dt>
        <dd className="proposal-card__value">{proposal.reason}</dd>
      </dl>
      <form className="proposal-card__actions" action={reviewProposal}>
        <input type="hidden" name="proposalId" value={proposal.id} />
        {proposal.status !== "approved" && <ReviewButton status="approved" label="Approve" />}
        {proposal.status !== "rejected" && <ReviewButton status="rejected" label="Reject" />}
        {proposal.status !== "pending" && <ReviewButton status="pending" label="Undo review" />}
      </form>
    </article>
  );
}
