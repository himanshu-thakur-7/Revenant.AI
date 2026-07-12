// Convex schema — the truth ledger.
//
// The Python pipeline writes here (ghost/ledger.py in live mode); the console
// reads it so the whole run is visible remotely. Tables are schemaless
// (v.any) on purpose: the pipeline's pydantic models are the source of truth
// for shape, and the buildathon favors iteration speed over double-validation.

import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  sellers: defineTable(v.any()),
  campaigns: defineTable(v.any()),
  events: defineTable(v.any()), // the mission log — one row per agent action
  memories: defineTable(v.any()), // persistence-engine fuel
  beacons: defineTable(v.any()), // anonymized microsite engagement
});
