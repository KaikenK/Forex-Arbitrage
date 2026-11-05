/* Research calendar the UI counts down to (RESEARCH_TRACKER §6). Times are UTC instants. */

/** RQ5 extension, C40, the prospective test and the C43 shadow analysis open, and the
 *  Phase 2 switches go on: 2026-11-18 00:00 IST. */
export const SEALED_UNTIL = "2026-11-17T18:30:00Z";

/** The arrival-latency sample closes 2026-11-13 23:59 IST; its analysis runs from 14 Nov. */
export const LATENCY_OPENS = "2026-11-13T18:30:00Z";

export const API_BASE_URL = process.env.NEXT_PUBLIC_ARBEX_API_BASE_URL || "http://127.0.0.1:8000";
