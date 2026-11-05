"use client";

import React from "react";
import summary from "@/data/research_summary.json";
import { Chip, type Tone } from "@/components/terminal/primitives";

/** Three one-line results behind "news as risk, not direction" (from the committed artifacts). */
export function EvidenceStrip() {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {summary.evidence.map((e) => (
        <span key={e.label} className="flex items-center gap-1.5 text-[11px] text-ink-3">
          {e.label}
          <Chip tone={e.tone as Tone}>{e.value}</Chip>
        </span>
      ))}
    </div>
  );
}
