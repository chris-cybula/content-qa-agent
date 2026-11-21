// Chat box
// Client form that sends the conversation and shows answers and proposals.

"use client";

import Link from "next/link";
import { useActionState } from "react";

import { sendChatMessage } from "@/app/actions";
import { emptyChat, maxQuestionLength } from "@/lib/chat";
import { shortUrl } from "@/lib/format";

export function ChatBox({ runId }: { runId: number }) {
  const [state, formAction, pending] = useActionState(sendChatMessage, emptyChat);

  return (
    <div className="chat__body">
      {state.messages.length === 0 ? (
        <p className="chat__empty">
          Ask about this run, for example “Which page has the most problems?” or “Fix the alt text
          problems on the home page.”
        </p>
      ) : (
        <ol className="chat__log">
          {state.messages.map((message, index) => (
            <li className={`chat__message chat__message--${message.role}`} key={index}>
              <span className="chat__role">{message.role === "user" ? "You" : "Claude"}</span>
              <p className="chat__text">{message.text}</p>
            </li>
          ))}
        </ol>
      )}

      {pending && (
        <p className="chat__status" role="status">
          Claude is reading the run…
        </p>
      )}
      {state.notice && (
        <p className="chat__notice" role="alert">
          {state.notice}
        </p>
      )}

      {state.proposals.length > 0 && (
        <section className="chat__proposals" aria-labelledby="chat-proposals-title">
          <h2 className="chat__proposals-title" id="chat-proposals-title">
            Proposed in this chat
          </h2>
          <ul className="chat__proposal-list">
            {state.proposals.map((proposal) => (
              <li className="chat__proposal" key={proposal.id}>
                <span className="chat__proposal-rule">{proposal.rule}</span> on{" "}
                {shortUrl(proposal.page_url)}: “{proposal.new_text}”
              </li>
            ))}
          </ul>
          <Link className="chat__review-link" href={`/runs/${runId}#proposals`}>
            Approve or reject them on the run page →
          </Link>
        </section>
      )}

      <form className="chat__form" action={formAction}>
        <input type="hidden" name="runId" value={runId} />
        <label className="chat__label" htmlFor="chat-question">
          Your message
        </label>
        <textarea
          className="chat__input"
          id="chat-question"
          name="question"
          rows={3}
          maxLength={maxQuestionLength}
          required
        />
        <div className="chat__actions">
          <button className="chat__send" type="submit" disabled={pending} aria-disabled={pending}>
            {pending ? "Sending…" : "Send"}
          </button>
          <span className="chat__hint">
            Each message takes a few Claude calls, all inside the monthly budget.
          </span>
        </div>
      </form>
    </div>
  );
}
