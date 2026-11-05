"use client";

import React from "react";
import { EmptyState, Panel } from "@/components/terminal/primitives";

export default function ResearchView() {
  return (
    <div className="h-full p-px">
      <Panel title="Research" className="h-full">
        <EmptyState title="Research view" detail="Being built on the ui-updates branch." />
      </Panel>
    </div>
  );
}
