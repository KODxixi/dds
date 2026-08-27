export class ApiError extends Error {
  constructor(message, { code = "REQUEST_FAILED", retryable = false, status = 0, details = null } = {}) {
    super(message || "请求失败");
    this.name = "ApiError";
    this.code = code;
    this.retryable = Boolean(retryable);
    this.status = Number(status || 0);
    this.details = details;
  }
}

const TERMINAL_SUCCESS = new Set(["done", "completed", "success", "decision_ready"]);
const TERMINAL_ERROR = new Set(["error", "failed", "cancelled"]);

function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason || new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = globalThis.setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      globalThis.clearTimeout(timer);
      reject(signal.reason || new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

function parseJson(text) {
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { message: text };
  }
}

function extractJob(payload) {
  if (!payload || typeof payload !== "object") return {};
  return payload.job || payload.data?.job || payload.data || payload;
}

function terminalState(payload) {
  const job = extractJob(payload);
  if (job.error?.message) job.message = job.error.message;
  const status = String(job.status || job.state || payload?.type || "").toLowerCase();
  if (TERMINAL_SUCCESS.has(status)) return "success";
  if (TERMINAL_ERROR.has(status)) return "error";
  return null;
}

export class ApiClient {
  constructor({ baseUrl = "/api/v2", fetchImpl = globalThis.fetch, pollInterval = 1500 } = {}) {
    if (typeof fetchImpl !== "function") throw new TypeError("fetch is required");
    this.baseUrl = String(baseUrl).replace(/\/$/, "");
    this.fetchImpl = fetchImpl.bind(globalThis);
    this.pollInterval = Math.max(500, Number(pollInterval || 1500));
    this._suggestController = null;
  }

  _url(path = "", query = null) {
    const origin = globalThis.location?.origin || "http://localhost";
    const base = /^[a-z][a-z\d+.-]*:/i.test(this.baseUrl)
      ? this.baseUrl
      : new URL(this.baseUrl, origin).toString();
    const url = new URL(`${base.replace(/\/$/, "")}/${String(path).replace(/^\/+/, "")}`);
    if (query) {
      Object.entries(query).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
      });
    }
    return url;
  }

  async request(path, { method = "GET", query, body, signal, headers = {} } = {}) {
    const requestHeaders = { Accept: "application/json", ...headers };
    const options = { method, headers: requestHeaders, signal, credentials: "same-origin" };
    if (body !== undefined) {
      requestHeaders["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await this.fetchImpl(this._url(path, query), options);
    } catch (error) {
      if (error?.name === "AbortError") throw error;
      throw new ApiError("无法连接 DDS 服务", {
        code: "NETWORK_ERROR",
        retryable: true,
        details: { cause: String(error?.message || error) },
      });
    }

    const payload = parseJson(await response.text());
    if (!response.ok || payload?.status === "error") {
      throw new ApiError(payload?.message || `请求失败（${response.status}）`, {
        code: payload?.code || `HTTP_${response.status}`,
        retryable: payload?.retryable ?? response.status >= 500,
        status: response.status,
        details: payload?.details || payload,
      });
    }
    return payload;
  }

  bootstrap(signal) {
    return this.request("bootstrap", { signal });
  }

  suggest(query, city = "", signal) {
    this._suggestController?.abort();
    this._suggestController = new AbortController();
    const requestSignal = signal && globalThis.AbortSignal?.any
      ? AbortSignal.any([signal, this._suggestController.signal])
      : this._suggestController.signal;
    return this.request("location/suggest", {
      query: { q: String(query || "").trim(), city },
      signal: requestSignal,
    });
  }

  cancelSuggest() {
    this._suggestController?.abort();
    this._suggestController = null;
  }

  resolveLocation(input, signal) {
    const body = typeof input === "string" ? { query: input } : { ...(input || {}) };
    return this.request("location/resolve", { method: "POST", body, signal });
  }

  createProject(location, signal) {
    return this.request("projects", { method: "POST", body: { location }, signal });
  }

  getProject(projectId, signal) {
    return this.request(`projects/${encodeURIComponent(projectId)}`, { signal });
  }

  answer(projectId, answer, signal) {
    return this.request(`projects/${encodeURIComponent(projectId)}/answers`, {
      method: "POST",
      body: answer,
      signal,
    });
  }

  startAnalysis(projectId, signal) {
    return this.request(`projects/${encodeURIComponent(projectId)}/analysis`, {
      method: "POST",
      body: {},
      signal,
    });
  }

  getJob(jobId, signal) {
    return this.request(`jobs/${encodeURIComponent(jobId)}`, { signal });
  }

  getReportData(projectId, signal) {
    return this.request(`projects/${encodeURIComponent(projectId)}/report-data`, { signal });
  }

  createReport(projectId, revision, signal) {
    const body = revision == null ? {} : { revision };
    return this.request(`projects/${encodeURIComponent(projectId)}/report`, {
      method: "POST",
      body,
      signal,
    });
  }

  exportProject(projectId, format, revision, signal) {
    const body = { format };
    if (revision != null) body.revision = revision;
    return this.request(`projects/${encodeURIComponent(projectId)}/export`, {
      method: "POST",
      body,
      signal,
    });
  }

  async watchJob(jobId, { onUpdate, onConnection, signal, pollInterval = this.pollInterval } = {}) {
    const notify = (payload) => {
      onUpdate?.(payload, extractJob(payload));
      return terminalState(payload);
    };

    const poll = async () => {
      onConnection?.("polling");
      while (!signal?.aborted) {
        const payload = await this.getJob(jobId, signal);
        const terminal = notify(payload);
        if (terminal === "success") return payload;
        if (terminal === "error") {
          const job = extractJob(payload);
          throw new ApiError(job.error?.message || job.message || "分析任务失败", {
            code: job.code || "JOB_FAILED",
            retryable: job.retryable ?? true,
            details: job,
          });
        }
        await delay(pollInterval, signal);
      }
      throw signal?.reason || new DOMException("Aborted", "AbortError");
    };

    if (typeof globalThis.EventSource !== "function") return poll();

    return new Promise((resolve, reject) => {
      let source;
      let settled = false;
      let receivedEvent = false;

      const cleanup = () => {
        source?.close();
        signal?.removeEventListener("abort", abort);
      };
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        cleanup();
        callback(value);
      };
      const abort = () => finish(reject, signal?.reason || new DOMException("Aborted", "AbortError"));
      const consume = (event) => {
        if (typeof event.data !== 'string') return;
        receivedEvent = true;
        const payload = parseJson(event.data);
        if (event.type && event.type !== "message") payload.type ||= event.type;
        const terminal = notify(payload);
        if (terminal === "success") finish(resolve, payload);
        if (terminal === "error") {
          const job = extractJob(payload);
          finish(reject, new ApiError(job.error?.message || job.message || "分析任务失败", {
            code: job.code || "JOB_FAILED",
            retryable: job.retryable ?? true,
            details: job,
          }));
        }
      };

      signal?.addEventListener("abort", abort, { once: true });
      source = new EventSource(this._url(`jobs/${encodeURIComponent(jobId)}/events`).toString());
      source.onopen = () => onConnection?.("sse");
      source.onmessage = consume;
      source.addEventListener('job', consume);
      ["stage", "progress", "warning", "done", "error"].forEach((type) => {
        source.addEventListener(type, consume);
      });
      source.onerror = () => {
        if (settled) return;
        cleanup();
        onConnection?.(receivedEvent ? "reconnecting" : "polling");
        poll().then(
          (payload) => finish(resolve, payload),
          (error) => finish(reject, error),
        );
      };
    });
  }
}
