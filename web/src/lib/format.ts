// Formatters
// Dates, durations, costs and short URLs for display.

const dateFormat = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/London",
});

export function formatDate(iso: string): string {
  return dateFormat.format(new Date(iso));
}

export function formatCost(usd: string): string {
  const value = Number(usd);
  if (value === 0) {
    return "$0";
  }
  return `$${value.toFixed(4)}`;
}

export function formatDuration(ms: number): string {
  if (ms === 0) {
    return "—";
  }
  const seconds = ms / 1000;
  if (seconds < 60) {
    return `${seconds.toFixed(1)} s`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds % 60)} s`;
}

export function crawlDuration(startedAt: string, finishedAt: string | null): string {
  if (finishedAt === null) {
    return "—";
  }
  return formatDuration(new Date(finishedAt).getTime() - new Date(startedAt).getTime());
}

export function shortUrl(url: string): string {
  const parsed = new URL(url);
  return `${parsed.pathname}${parsed.search}`;
}
