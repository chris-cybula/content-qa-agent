// Report download
// Proxies the API's CSV or HTML report as a file download.

import { apiUrl } from "@/lib/api";

const formats = ["csv", "html"];

export async function GET(
  _request: Request,
  context: RouteContext<"/runs/[runId]/report/[format]">,
) {
  const { runId, format } = await context.params;
  const id = Number(runId);

  if (!Number.isInteger(id) || id <= 0 || !formats.includes(format)) {
    return new Response("Not found", { status: 404 });
  }

  const response = await fetch(`${apiUrl}/runs/${id}/report?format=${format}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    return new Response(`The API answered ${response.status}`, { status: response.status });
  }

  return new Response(response.body, {
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/octet-stream",
      "content-disposition":
        response.headers.get("content-disposition") ?? `attachment; filename="run-${id}.${format}"`,
    },
  });
}
