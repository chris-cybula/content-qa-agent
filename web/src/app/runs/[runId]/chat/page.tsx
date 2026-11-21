// Chat page (/runs/[runId]/chat)
// Agent mode for one run. Each message is a paid Claude call.

import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { getRun, NotFoundError, type RunSummary } from "@/lib/api";

import { ChatBox } from "./chat-box";

async function ChatView({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const id = Number(runId);
  if (!Number.isInteger(id) || id <= 0) {
    notFound();
  }

  let run: RunSummary;
  try {
    run = await getRun(id);
  } catch (error) {
    if (error instanceof NotFoundError) {
      notFound();
    }
    throw error;
  }

  return (
    <>
      <Link className="section__link" href={`/runs/${run.id}`}>
        ← Run {run.id}
      </Link>
      <section className="chat">
        <header className="chat__header">
          <h1 className="chat__title">Chat about run {run.id}</h1>
          <p className="chat__intro">{run.sitemap_url}</p>
          <p className="chat__intro">
            Claude reads this run’s saved pages and findings. It can only propose fixes; you approve
            or reject them on the run page.
          </p>
        </header>
        <ChatBox runId={run.id} />
      </section>
    </>
  );
}

export default function ChatPage({ params }: PageProps<"/runs/[runId]/chat">) {
  return (
    <Suspense fallback={<p className="section__note">Loading chat…</p>}>
      <ChatView params={params} />
    </Suspense>
  );
}
