"use client";

import React from "react";
import { EmptyState, Panel } from "@/components/terminal/primitives";

export default function HealthView() {
  return (
    <div className="h-full p-px">
      <Panel title="Health" className="h-full">
        <EmptyState title="Health view" detail="Being built on the ui-updates branch." />
      </Panel>
    </div>
  );
}
