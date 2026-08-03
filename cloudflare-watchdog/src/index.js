const SEOUL_TIME_ZONE = "Asia/Seoul";
const ACTIVE_RUN_STATUSES = new Set([
  "queued",
  "in_progress",
  "waiting",
  "pending",
  "requested",
]);

const WORKFLOWS = {
  reservation: "update-es90-reservations.yml",
  charger: "update-charger-data.yml",
  competitor: "update-getcha-prices.yml",
};

const STALE_RUN_ENV_KEYS = {
  reservation: "RESERVATION_STALE_RUN_MINUTES",
  charger: "CHARGER_STALE_RUN_MINUTES",
  competitor: "COMPETITOR_STALE_RUN_MINUTES",
};

const DEFAULT_STALE_RUN_MINUTES = {
  reservation: 20,
  charger: 35,
  competitor: 20,
};

function pad2(value) {
  return String(value).padStart(2, "0");
}

export function getSeoulClock(now) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: SEOUL_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const values = Object.fromEntries(
    parts.filter(({ type }) => type !== "literal").map(({ type, value }) => [type, value]),
  );
  const date = `${values.year}-${values.month}-${values.day}`;
  return {
    date,
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    hour: Number(values.hour),
    minute: Number(values.minute),
    second: Number(values.second),
    minuteOfDay: Number(values.hour) * 60 + Number(values.minute),
  };
}

function parseTimestamp(value) {
  if (!value || typeof value !== "string") {
    return Number.NaN;
  }
  return Date.parse(value);
}

function seoulDateForTimestamp(value) {
  const timestamp = parseTimestamp(value);
  if (!Number.isFinite(timestamp)) {
    return null;
  }
  return getSeoulClock(new Date(timestamp)).date;
}

function seoulTimestamp(date, hour, minute = 0) {
  return Date.parse(`${date}T${pad2(hour)}:${pad2(minute)}:00+09:00`);
}

export function evaluateRequiredWork(version, now = new Date()) {
  const clock = getSeoulClock(now);
  const work = [];

  if (clock.hour >= 8) {
    const targetHour = Math.min(clock.hour, 18);
    const targetMs = seoulTimestamp(clock.date, targetHour);
    const observedMs = parseTimestamp(version?.reservationUpdatedAt);
    if (!Number.isFinite(observedMs) || observedMs < targetMs) {
      work.push({
        key: "reservation",
        workflow: WORKFLOWS.reservation,
        targetMs,
        target: `${clock.date} ${pad2(targetHour)}:00 KST`,
        reason: `reservationUpdatedAt=${version?.reservationUpdatedAt || "missing"}`,
      });
    }
  }

  if (clock.minuteOfDay >= 6 * 60 + 20) {
    const chargerDate = seoulDateForTimestamp(version?.chargerCheckedAt);
    const tmapDate = seoulDateForTimestamp(version?.tmapCheckedAt);
    if (chargerDate !== clock.date || tmapDate !== clock.date) {
      work.push({
        key: "charger",
        workflow: WORKFLOWS.charger,
        targetMs: seoulTimestamp(clock.date, 6, 20),
        target: `${clock.date} daily charger/TMAP check`,
        reason: `charger=${chargerDate || "missing"}, tmap=${tmapDate || "missing"}`,
      });
    }
  }

  if (clock.minuteOfDay >= 7 * 60 + 17) {
    const competitorDate = seoulDateForTimestamp(version?.competitorPriceCheckedAt);
    if (competitorDate !== clock.date) {
      work.push({
        key: "competitor",
        workflow: WORKFLOWS.competitor,
        targetMs: seoulTimestamp(clock.date, 7, 17),
        target: `${clock.date} daily competitor-price check`,
        reason: `competitorPriceCheckedAt=${version?.competitorPriceCheckedAt || "missing"}`,
      });
    }
  }

  return { clock, work };
}

function withCacheBuster(url, now) {
  const target = new URL(url);
  target.searchParams.set("watchdog", String(now.getTime()));
  return target.toString();
}

async function fetchJson(url, fetchImpl, now, headers = {}) {
  const response = await fetchImpl(withCacheBuster(url, now), {
    headers: {
      accept: "application/json",
      "cache-control": "no-cache",
      ...headers,
    },
  });
  if (!response.ok) {
    throw new Error(`GET ${url} returned HTTP ${response.status}`);
  }
  return response.json();
}

function githubHeaders(token) {
  return {
    accept: "application/vnd.github+json",
    authorization: `Bearer ${token}`,
    "user-agent": "es90-matrix-cloudflare-watchdog",
    "x-github-api-version": "2026-03-10",
  };
}

function githubWorkflowUrl(env, workflow) {
  const owner = encodeURIComponent(env.GITHUB_OWNER || "volvo-es90-matrix");
  const repo = encodeURIComponent(env.GITHUB_REPO || "es90-matrix");
  return `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${encodeURIComponent(workflow)}`;
}

async function readWorkflowRuns(env, workflow, fetchImpl, now) {
  const url = `${githubWorkflowUrl(env, workflow)}/runs?per_page=10`;
  const response = await fetchImpl(withCacheBuster(url, now), {
    headers: githubHeaders(env.GITHUB_WORKFLOW_TOKEN),
  });
  if (!response.ok) {
    throw new Error(`GitHub run lookup for ${workflow} returned HTTP ${response.status}`);
  }
  const payload = await response.json();
  return Array.isArray(payload.workflow_runs) ? payload.workflow_runs : [];
}

async function dispatchWorkflow(env, workflow, fetchImpl) {
  const url = `${githubWorkflowUrl(env, workflow)}/dispatches`;
  const response = await fetchImpl(url, {
    method: "POST",
    headers: {
      ...githubHeaders(env.GITHUB_WORKFLOW_TOKEN),
      "content-type": "application/json",
    },
    body: JSON.stringify({ ref: env.GITHUB_REF || "main" }),
  });
  if (!response.ok) {
    const details = await response.text();
    throw new Error(
      `GitHub dispatch for ${workflow} returned HTTP ${response.status}: ${details.slice(0, 300)}`,
    );
  }
  if (response.status === 204) {
    return {};
  }
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

async function cancelWorkflowRun(env, runId, fetchImpl) {
  const owner = encodeURIComponent(env.GITHUB_OWNER || "volvo-es90-matrix");
  const repo = encodeURIComponent(env.GITHUB_REPO || "es90-matrix");
  const url = `https://api.github.com/repos/${owner}/${repo}/actions/runs/${encodeURIComponent(runId)}/cancel`;
  const response = await fetchImpl(url, {
    method: "POST",
    headers: githubHeaders(env.GITHUB_WORKFLOW_TOKEN),
  });
  if (!response.ok) {
    throw new Error(
      `GitHub cancellation for run ${runId} returned HTTP ${response.status}`,
    );
  }
}

function findActiveRun(runs) {
  return runs.find((run) => ACTIVE_RUN_STATUSES.has(run.status));
}

function findLatestRun(runs) {
  return [...runs]
    .filter((run) => Number.isFinite(Date.parse(run.created_at)))
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
}

function staleRunThresholdMs(item, env) {
  const configured = Number(env[STALE_RUN_ENV_KEYS[item.key]]);
  const minutes = Number.isFinite(configured)
    ? Math.max(5, configured)
    : DEFAULT_STALE_RUN_MINUTES[item.key];
  return minutes * 60 * 1000;
}

function runAgeMs(run, now) {
  const startedAt = Date.parse(run.run_started_at || run.created_at);
  return Number.isFinite(startedAt) ? now.getTime() - startedAt : Number.POSITIVE_INFINITY;
}

async function processWorkItem(item, env, options) {
  const { now, fetchImpl, dryRun } = options;
  const runs = await readWorkflowRuns(env, item.workflow, fetchImpl, now);
  const activeRun = findActiveRun(runs);
  if (activeRun) {
    if (runAgeMs(activeRun, now) >= staleRunThresholdMs(item, env)) {
      if (dryRun) {
        return {
          key: item.key,
          status: "would_cancel_and_dispatch",
          workflow: item.workflow,
          staleRunId: activeRun.id,
          url: activeRun.html_url || null,
        };
      }
      await cancelWorkflowRun(env, activeRun.id, fetchImpl);
      const dispatch = await dispatchWorkflow(env, item.workflow, fetchImpl);
      return {
        key: item.key,
        status: "cancelled_stale_and_dispatched",
        workflow: item.workflow,
        staleRunId: activeRun.id,
        target: item.target,
        reason: item.reason,
        runId: dispatch.workflow_run_id || null,
        url: dispatch.html_url || null,
      };
    }
    return {
      key: item.key,
      status: "active",
      workflow: item.workflow,
      url: activeRun.html_url || null,
    };
  }

  const latestRun = findLatestRun(runs);
  const latestRunMs = latestRun ? Date.parse(latestRun.created_at) : Number.NaN;
  const cooldownMinutes = Number(env.RETRY_COOLDOWN_MINUTES || 15);
  const cooldownMs = Math.max(5, cooldownMinutes) * 60 * 1000;
  if (
    Number.isFinite(latestRunMs) &&
    latestRunMs >= item.targetMs &&
    now.getTime() - latestRunMs < cooldownMs
  ) {
    return {
      key: item.key,
      status: "cooldown",
      workflow: item.workflow,
      lastRunAt: latestRun.created_at,
      conclusion: latestRun.conclusion || null,
      url: latestRun.html_url || null,
    };
  }

  if (dryRun) {
    return {
      key: item.key,
      status: "would_dispatch",
      workflow: item.workflow,
      target: item.target,
      reason: item.reason,
    };
  }

  const dispatch = await dispatchWorkflow(env, item.workflow, fetchImpl);
  return {
    key: item.key,
    status: "dispatched",
    workflow: item.workflow,
    target: item.target,
    reason: item.reason,
    runId: dispatch.workflow_run_id || null,
    url: dispatch.html_url || null,
  };
}

export async function runWatchdog(env, options = {}) {
  const now = options.now || new Date();
  const fetchImpl = options.fetchImpl || fetch;
  const dryRun = Boolean(options.dryRun);
  const result = {
    ok: true,
    trigger: options.trigger || "unknown",
    checkedAt: now.toISOString(),
    dryRun,
    repositoryVersion: null,
    pagesVersion: null,
    githubAuthVerified: false,
    required: [],
    actions: [],
    warnings: [],
    errors: [],
  };

  try {
    result.repositoryVersion = await fetchJson(
      env.REPOSITORY_VERSION_URL,
      fetchImpl,
      now,
    );
  } catch (error) {
    result.ok = false;
    result.errors.push(error instanceof Error ? error.message : String(error));
    return result;
  }

  try {
    result.pagesVersion = await fetchJson(env.PAGES_VERSION_URL, fetchImpl, now);
  } catch (error) {
    result.warnings.push(error instanceof Error ? error.message : String(error));
  }

  const evaluation = evaluateRequiredWork(result.repositoryVersion, now);
  result.seoulTime = `${evaluation.clock.date} ${pad2(evaluation.clock.hour)}:${pad2(evaluation.clock.minute)}:${pad2(evaluation.clock.second)}`;
  result.required = evaluation.work.map(({ key, workflow, target, reason }) => ({
    key,
    workflow,
    target,
    reason,
  }));

  if (
    result.pagesVersion &&
    result.pagesVersion.updatedAt !== result.repositoryVersion.updatedAt
  ) {
    result.warnings.push(
      `GitHub Pages version is behind the repository: pages=${result.pagesVersion.updatedAt}, repo=${result.repositoryVersion.updatedAt}`,
    );
  }

  try {
    await readWorkflowRuns(env, WORKFLOWS.reservation, fetchImpl, now);
    result.githubAuthVerified = true;
  } catch (error) {
    result.ok = false;
    result.errors.push(
      `github-auth: ${error instanceof Error ? error.message : String(error)}`,
    );
    return result;
  }

  for (const item of evaluation.work) {
    try {
      result.actions.push(
        await processWorkItem(item, env, { now, fetchImpl, dryRun }),
      );
    } catch (error) {
      result.ok = false;
      result.errors.push(
        `${item.key}: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  return result;
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

async function isAuthorized(request, env) {
  const expected = env.WATCHDOG_SHARED_SECRET;
  if (!expected) {
    return false;
  }
  const supplied = request.headers.get("authorization") || "";
  const encoder = new TextEncoder();
  const [expectedDigest, suppliedDigest] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(`Bearer ${expected}`)),
    crypto.subtle.digest("SHA-256", encoder.encode(supplied)),
  ]);
  if (typeof crypto.subtle.timingSafeEqual === "function") {
    return crypto.subtle.timingSafeEqual(expectedDigest, suppliedDigest);
  }
  const expectedBytes = new Uint8Array(expectedDigest);
  const suppliedBytes = new Uint8Array(suppliedDigest);
  let difference = 0;
  for (let index = 0; index < expectedBytes.length; index += 1) {
    difference |= expectedBytes[index] ^ suppliedBytes[index];
  }
  return difference === 0;
}

export default {
  async scheduled(controller, env) {
    const result = await runWatchdog(env, {
      now: new Date(controller.scheduledTime),
      trigger: `cron:${controller.cron}`,
    });
    console.log(JSON.stringify(result));
    if (!result.ok) {
      throw new Error(result.errors.join("; "));
    }
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return jsonResponse({
        ok: true,
        service: "es90-matrix-watchdog",
        schedule: "every 5 minutes at :03, :08, ... :58",
      });
    }

    if (request.method !== "POST" || url.pathname !== "/run") {
      return jsonResponse({ error: "Not found" }, 404);
    }
    if (!(await isAuthorized(request, env))) {
      return jsonResponse({ error: "Unauthorized" }, 401);
    }

    const result = await runWatchdog(env, {
      trigger: "manual-http",
      dryRun: url.searchParams.get("dry_run") === "true",
    });
    return jsonResponse(result, result.ok ? 200 : 500);
  },
};
