/* Reading the semantic channels of a scored cross for display (no scoring happens here). */
import type { RankedOpportunity } from "@/lib/store";

export interface RiskView {
  available: boolean;
  penalty: number;
  reason: string;
  expectedMoveBps: number | null;
  riskRatio: number | null;
  proximity: string | null;
}

export function proximityText(p: unknown): string | null {
  if (p === null || p === undefined) return null;
  if (typeof p !== "object") return String(p);
  const o = p as { phase?: string; minutes?: number; currency?: string; event?: string };
  const what = `${o.currency ?? ""} ${o.event ?? ""}`.trim();
  if (o.phase === "pre_release") return `in ${o.minutes} min: ${what}`;
  if (o.phase === "post_release") return `${o.minutes} min ago: ${what}`;
  return what || null;
}

export function riskOf(opp: RankedOpportunity): RiskView {
  const risk = opp.semantic_channels?.risk;
  if (!risk) {
    return { available: false, penalty: 0, reason: "risk channel not in this message", expectedMoveBps: null,
             riskRatio: null, proximity: null };
  }
  const inputs = risk.inputs ?? {};
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  return {
    available: true,
    penalty: risk.penalty ?? 0,
    reason: risk.reason ?? "",
    expectedMoveBps: num(inputs.expected_move_bps),
    riskRatio: num(inputs.risk_ratio),
    proximity: proximityText(inputs.release_proximity),
  };
}

/** True when the dashboard scorer applied the direction channel (the C43 display does not). */
export function directionApplied(opp: RankedOpportunity) {
  return !!opp.semantic_channels && !opp.ranking_reason.includes("direction not applied");
}

/** The detector's own confidence. From the shadow block when present; otherwise rebuilt from the
 *  displayed score by undoing the window adjustment and the applied channels (marked as rebuilt). */
export function detectorScore(opp: RankedOpportunity): { value: number; rebuilt: boolean } {
  const fromShadow = opp.shadow?.confidence_score;
  if (typeof fromShadow === "number") return { value: fromShadow, rebuilt: false };
  const shown = Number(opp.opportunity.confidence_score) || 0;
  const windowAdj = opp.news_bias?.confidence_adjustment ?? 0;
  const risk = opp.semantic_channels?.risk?.penalty ?? 0;
  const direction = directionApplied(opp) ? opp.semantic_channels?.direction?.penalty ?? 0 : 0;
  return { value: Math.min(1, Math.max(0, shown - windowAdj + risk + direction)), rebuilt: true };
}

/* The risk channel's constants (backend/core/currency_bias_channels.py), used only to show what the
   channel would take off a cross on this pair right now. */
export const RISK_CAP = 0.06;
export const RISK_RATIO_SPAN = 2.0;
export const RELEASE_PENALTY = 0.03;

export interface EventRisk {
  expected_move_bps?: number | null;
  baseline_move_bps?: number | null;
  risk_ratio?: number | null;
  release_proximity?: unknown;
  next_release?: { minutes?: number; event?: string } | null;
  last_release?: { minutes?: number; event?: string } | null;
  calendar?: string;
  recent_scored_items?: number;
}

export function riskPenaltyNow(er: EventRisk | null | undefined): number {
  if (!er) return 0;
  let penalty = 0;
  if (typeof er.risk_ratio === "number") {
    penalty = RISK_CAP * Math.min(1, Math.max(0, (er.risk_ratio - 1) / RISK_RATIO_SPAN));
  }
  if (er.release_proximity !== null && er.release_proximity !== undefined) penalty += RELEASE_PENALTY;
  return Math.min(RISK_CAP, penalty);
}

export const DIRECTION_LABEL: Record<string, string> = {
  risk_on: "pair up", risk_off: "pair down", neutral: "neutral",
  bullish: "pair up", bearish: "pair down",
};
