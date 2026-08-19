// Thin client for the public Integration API contract (contracts/openapi.yaml).
// Paths are relative: the Vite dev proxy forwards /v1 to the API.
export function submitQuery(token, messages, signal) {
  return fetch("/v1/query", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ messages }),
    signal,
  });
}

export async function getJob(token, jobId) {
  const res = await fetch(`/v1/jobs/${jobId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return { status: res.status, body: await res.json() };
}
