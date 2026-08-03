import assert from "node:assert/strict";
import test from "node:test";

import { evaluateRequiredWork, getSeoulClock, runWatchdog } from "../src/index.js";

const baseVersion = {
  reservationUpdatedAt: "2026-08-03T09:03:00+09:00",
  chargerCheckedAt: "2026-08-03T06:30:00+09:00",
  tmapCheckedAt: "2026-08-03T06:30:00+09:00",
  competitorPriceCheckedAt: "2026-08-03T07:30:00+09:00",
};

test("converts UTC to the correct Seoul clock", () => {
  assert.deepEqual(getSeoulClock(new Date("2026-08-03T01:03:04Z")), {
    date: "2026-08-03",
    year: 2026,
    month: 8,
    day: 3,
    hour: 10,
    minute: 3,
    second: 4,
    minuteOfDay: 603,
  });
});

test("requires the current reservation slot when the observation is stale", () => {
  const result = evaluateRequiredWork(baseVersion, new Date("2026-08-03T01:03:00Z"));
  assert.deepEqual(result.work.map(({ key }) => key), ["reservation"]);
  assert.equal(result.work[0].target, "2026-08-03 10:00 KST");
});

test("does not require a reservation run when the current slot is complete", () => {
  const version = {
    ...baseVersion,
    reservationUpdatedAt: "2026-08-03T10:02:00+09:00",
  };
  const result = evaluateRequiredWork(version, new Date("2026-08-03T01:08:00Z"));
  assert.deepEqual(result.work, []);
});

test("keeps monitoring the final 18:00 reservation slot after business hours", () => {
  const result = evaluateRequiredWork(baseVersion, new Date("2026-08-03T12:03:00Z"));
  const reservation = result.work.find(({ key }) => key === "reservation");
  assert.equal(reservation.target, "2026-08-03 18:00 KST");
});

test("stops requesting the final slot after it is confirmed", () => {
  const version = {
    ...baseVersion,
    reservationUpdatedAt: "2026-08-03T18:04:00+09:00",
  };
  const result = evaluateRequiredWork(version, new Date("2026-08-03T14:03:00Z"));
  assert.equal(result.work.some(({ key }) => key === "reservation"), false);
});

test("requires charger and competitor checks after their daily start times", () => {
  const version = {
    ...baseVersion,
    chargerCheckedAt: "2026-08-02T18:20:00+09:00",
    tmapCheckedAt: "2026-08-02T18:20:00+09:00",
    competitorPriceCheckedAt: "2026-08-02T18:20:00+09:00",
  };
  const result = evaluateRequiredWork(version, new Date("2026-08-03T00:03:00Z"));
  assert.deepEqual(
    result.work.map(({ key }) => key),
    ["charger", "competitor"],
  );
});

test("treats a missing timestamp as stale", () => {
  const result = evaluateRequiredWork({}, new Date("2026-08-03T00:03:00Z"));
  assert.deepEqual(
    result.work.map(({ key }) => key),
    ["reservation", "charger", "competitor"],
  );
});

test("cancels and replaces an active reservation run that is stuck", async () => {
  const now = new Date("2026-08-03T10:03:00Z"); // 19:03 KST
  const version = {
    ...baseVersion,
    reservationUpdatedAt: "2026-08-03T17:58:00+09:00",
  };
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    const method = options.method || "GET";
    calls.push({ url: String(url), method });
    if (String(url).includes("version.json")) {
      return Response.json(version);
    }
    if (String(url).includes("/runs?")) {
      return Response.json({
        workflow_runs: [{
          id: 123,
          status: "in_progress",
          run_started_at: "2026-08-03T09:00:00Z",
          created_at: "2026-08-03T09:00:00Z",
          html_url: "https://github.com/example/run/123",
        }],
      });
    }
    if (String(url).endsWith("/actions/runs/123/cancel")) {
      return new Response(null, { status: 202 });
    }
    if (String(url).endsWith("/dispatches")) {
      return new Response(null, { status: 204 });
    }
    throw new Error(`Unexpected request: ${method} ${url}`);
  };
  const result = await runWatchdog({
    REPOSITORY_VERSION_URL: "https://repo.example/version.json",
    PAGES_VERSION_URL: "https://pages.example/version.json",
    GITHUB_OWNER: "owner",
    GITHUB_REPO: "repo",
    GITHUB_REF: "main",
    GITHUB_WORKFLOW_TOKEN: "test-token",
  }, { now, fetchImpl, trigger: "test" });

  assert.equal(result.ok, true);
  assert.equal(result.githubAuthVerified, true);
  assert.equal(result.actions[0].status, "cancelled_stale_and_dispatched");
  assert.equal(calls.some(({ url }) => url.endsWith("/actions/runs/123/cancel")), true);
  assert.equal(calls.some(({ url }) => url.endsWith("/dispatches")), true);
});
