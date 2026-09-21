let csrfToken: string | null = null;

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: string;
    };
    throw new Error(body.detail || `请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export async function getJson<T>(path: string): Promise<T> {
  return parseResponse<T>(
    await fetch(path, { headers: { Accept: "application/json" } }),
  );
}

async function getCsrfToken(): Promise<string> {
  if (csrfToken) return csrfToken;
  const payload = await getJson<{ csrf_token: string }>("/api/csrf");
  csrfToken = payload.csrf_token;
  return csrfToken;
}

export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const token = await getCsrfToken();
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": token,
    },
    body: JSON.stringify(body ?? {}),
  });
  if (response.status === 403) csrfToken = null;
  return parseResponse<T>(response);
}
