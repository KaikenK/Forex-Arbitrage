import { test, expect } from "@playwright/test";

const upstreamEvent = {
  event_id: "upstream-1",
  headline: "Reuters: RBI surprises with tighter liquidity guidance",
  source: "FINNHUB (Reuters)",
  pair: "USD/INR",
  category: "central_bank",
  timestamp: "2026-04-22T12:00:00+00:00",
  live: true,
  provenance: "upstream",
  provider: "finnhub",
  feed_mode: "live",
  upstream_status: "live",
  upstream_note: "Real upstream headlines are flowing into the feed.",
  last_upstream_item_at: "2026-04-22T12:00:00+00:00",
  feed_checked_at: "2026-04-22T12:00:00+00:00",
};

const syntheticEvent = {
  event_id: "synthetic-1",
  headline: "Fallback RBI scenario keeps rupee steady",
  source: "RSS",
  pair: "USD/INR",
  category: "rate_decision",
  timestamp: "2026-04-22T11:59:00+00:00",
  live: true,
  provenance: "synthetic",
  provider: "synthetic",
  feed_mode: "fallback",
  upstream_status: "fallback",
  upstream_note: "No relevant upstream headlines were available in the latest refresh; synthetic fallback may appear.",
  last_upstream_item_at: null,
  feed_checked_at: "2026-04-22T11:59:00+00:00",
};

async function installMockEventSource(page, events, delayMs = 40) {
  await page.addInitScript(
    ({ mockEvents, eventDelayMs }) => {
      class MockEventSource {
        constructor(url) {
          this.url = url;
          this.readyState = 1;
          setTimeout(() => {
            this.onopen?.({ type: "open" });
          }, 0);
          mockEvents.forEach((event, index) => {
            setTimeout(() => {
              this.onmessage?.({ data: JSON.stringify(event) });
            }, eventDelayMs * (index + 1));
          });
        }

        close() {}
      }

      window.EventSource = MockEventSource;
    },
    { mockEvents: events, eventDelayMs: delayMs },
  );
}

async function mockCommonDashboardApis(page, options = {}) {
  let analyzeCalls = 0;
  const historyItems = options.historyItems ?? [];
  const evaluationPayload = options.evaluationPayload ?? {
    magnitude_mae: 0.0012,
    direction_accuracy: 0.61,
    direction_macro_f1: 0.58,
    confusion_matrix: [[3, 1, 0], [1, 4, 0], [0, 1, 5]],
    calibration_score: 0.12,
    pair_breakdown: {
      "USD/INR": { count: 12, direction_accuracy: 0.61, magnitude_mae: 0.0012 },
    },
  };

  await page.route("**/history?*", async (route) => {
    const items = analyzeCalls > 0 && historyItems.length ? historyItems : [];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items }),
    });
  });

  await page.route("**/metrics", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        metrics: {
          analysis_requests: analyzeCalls,
          analyses_completed: analyzeCalls,
          average_confidence: analyzeCalls ? 0.71 : 0,
          history_size: analyzeCalls,
          last_analysis_at: analyzeCalls ? "2026-04-22T12:00:00+00:00" : null,
        },
      }),
    });
  });

  await page.route("**/evaluation", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(evaluationPayload),
    });
  });

  await page.route("**/sentiment/live", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        pair: "USD/INR",
        overall_sentiment: 0.18,
        confidence: 0.54,
        bullish_count: 2,
        bearish_count: 1,
        neutral_count: 0,
        item_count: 3,
        top_drivers: [],
        updated_at: "2026-04-22T12:00:00+00:00",
        window_minutes: 30,
      }),
    });
  });

  await page.route("**/analyze", async (route) => {
    analyzeCalls += 1;
    const request = route.request();
    const body = request.postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        result: {
          event_id: `evt-${analyzeCalls}`,
          sentiment_score: 0.62,
          market_score: -0.18,
          affected_symbols: [body.currency_pair || "USD/INR"],
          confidence: 0.71,
          evidence: [
            {
              evidence_id: "ev-1",
              source: "archive",
              relevance_score: 0.88,
              title: "Historical RBI analogue",
              snippet: "Prior RBI language surprise moved USD/INR lower intraday.",
            },
          ],
          risk_flags: ["HIGH_EVENT_RISK"],
          key_drivers: [{ label: "Liquidity guidance", rationale: "hawkish liquidity tone" }],
          trading_signal: "SELL_USD_BUY_INR",
          signal_rationale: "Historical analogues favor INR strength.",
          methodology: { scorer: "mock" },
        },
      }),
    });
  });
}

test("feed filtering, provenance, and click-to-fill work with live feed items", async ({ page }) => {
  await installMockEventSource(page, [syntheticEvent, upstreamEvent]);
  await mockCommonDashboardApis(page);
  await page.route("**/correlation?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ total: 0, offset: 0, limit: 50, items: [] }),
    });
  });

  await page.goto("/");

  await expect(page.locator("#feed-list .feed-item")).toHaveCount(2);
  await expect(page.locator("#feed-provenance-pill")).toHaveText("Upstream live");
  await expect(page.locator("#feed-list .provenance-badge--synthetic")).toBeVisible();
  await expect(page.locator("#feed-list .provenance-badge--upstream")).toBeVisible();

  await page.getByRole("button", { name: "Finnhub" }).click();
  await expect(page.locator("#feed-list .feed-item")).toHaveCount(1);
  await expect(page.getByText(upstreamEvent.headline)).toBeVisible();

  await page.getByText(upstreamEvent.headline).click();
  await expect(page.getByLabel("Headline *")).toHaveValue(upstreamEvent.headline);
  await expect(page.getByLabel("Source")).toHaveValue("FINNHUB");
  await expect(page.getByLabel("Currency pair")).toHaveValue("USD/INR");
});

test("auto-analyze updates results and validation panel", async ({ page }) => {
  await installMockEventSource(page, [upstreamEvent], 1000);
  await mockCommonDashboardApis(page, {
    historyItems: [
      {
        produced_at: "2026-04-22T12:00:00+00:00",
        market_score: -0.18,
      },
    ],
  });
  await page.route("**/correlation?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ total: 0, offset: 0, limit: 50, items: [] }),
    });
  });

  await page.goto("/");
  await page.locator("label.toggle").click();

  await expect(page.locator("#results-shell")).toBeVisible();
  await expect(page.locator("#signal-badge")).toHaveText("SELL USD BUY INR");
  await expect(page.locator("#topbar-metrics")).toHaveText("1 requests · 1 completed");
  await expect(page.locator("#feed-list .mini-badge").first()).toBeVisible();

  await page.locator("summary").filter({ hasText: "Model Validation" }).click();
  await expect(page.getByTestId("validation-body")).toContainText("61.0%");
  await expect(page.getByTestId("validation-body")).toContainText("58.0%");
});

test("correlation pagination loads multiple pages", async ({ page }) => {
  await installMockEventSource(page, []);
  await mockCommonDashboardApis(page);
  await page.route("**/correlation?*", async (route) => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get("offset") ?? "0");
    const pagePayload = offset >= 50
      ? {
          total: 75,
          offset: 50,
          limit: 50,
          items: [{ article_text: "Page 2 headline", reaction_label: -0.002, sentiment_label: -0.4, horizon_minutes: 240, pair: "USD/INR", timestamp: "2026-04-21T00:00:00+00:00" }],
        }
      : {
          total: 75,
          offset: 0,
          limit: 50,
          items: [{ article_text: "Page 1 headline", reaction_label: 0.003, sentiment_label: 0.6, horizon_minutes: 60, pair: "USD/INR", timestamp: "2026-04-22T00:00:00+00:00" }],
        };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(pagePayload),
    });
  });

  await page.goto("/");
  await page.locator('input[name="headline"]').fill("Correlation probe headline");
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page.locator("#results-shell")).toBeVisible();
  await page.locator("summary").filter({ hasText: "News-Price Correlation" }).click();

  await expect(page.getByTestId("correlation-body")).toContainText("Page 1 headline");
  await expect(page.locator("#corr-info")).toHaveText("1–50 of 75");

  await page.getByRole("button", { name: "Next →" }).click();
  await expect(page.getByTestId("correlation-body")).toContainText("Page 2 headline");
  await expect(page.locator("#corr-info")).toHaveText("51–75 of 75");

  await page.getByRole("button", { name: "← Prev" }).click();
  await expect(page.getByTestId("correlation-body")).toContainText("Page 1 headline");
});