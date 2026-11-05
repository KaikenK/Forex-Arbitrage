"use client";

import React from "react";
import { EmptyState, Panel } from "@/components/terminal/primitives";

export default function RiskView() {
  return (
    <div className="h-full p-px">
      <Panel title="Risk" className="h-full">
        <EmptyState title="Risk view" detail="Being built on the ui-updates branch." />
      </Panel>
    </div>
  );
}
