# Arbex V2 Tech Stack Migration Plan

This plan outlines the systematic migration of the Arbex terminal from a single-node Vanilla JS/FastAPI monolith into a production-ready **Next.js + Redis + Supabase SaaS platform**.

## User Review Required

> [!IMPORTANT]
> **Infrastructure Prerequisites:** 
> Before we begin Phase 2 & 3, we will need **Docker** installed on your machine to orchestrate Redis (for the Pub/Sub cache) and potentially a local Supabase instance, or you will need to provide cloud API keys for these services. 
> 
> *Are you ready to proceed with setting up the Next.js Frontend (Phase 1) locally using `npx create-next-app`?*

## Phase 1: The React/Next.js UI Overhaul
We will build the new frontend in a separate `arbex-web` directory to ensure we don't break the existing `run_server` pipeline while we develop.

### 1-A: Next.js Boilerplate
* Initialize a Next.js (App Router) project with TypeScript and Tailwind CSS.
* Install `shadcn/ui` components (Cards, Tables, Badges, Tabs).
* Install `framer-motion` for animated ranking lists.
* Install `zustand` for high-frequency state management.
* Install `lightweight-charts`.

### 1-B: Zustand State & WebSocket Hooks
* Build a highly optimized `useWebSocket` hook that receives JSON ticks.
* Build the Zustand store using `subscribeWithSelector` so React components bind *only* to specific data points (e.g., only re-rendering the +pips badge, not the whole screen).
* Port the Vanilla HTML layout into modular React components (`<ArbitrageDashboard>`, `<SessionRow>`, `<ExecutionPanel>`).

## Phase 2: The Redis Microservice Split
To scale the Python backend and extract the Semantic Engine, we will introduce Redis as our hot-path event bus.

### 2-A: Redis Pub/Sub Pipeline
* Introduce `redis.asyncio` to the FastAPI backend.
* Update [ArbitrageEngine](file:///c:/Users/dhruv/OneDrive/Desktop/Capstone/backend/core/arbitrage/arbitrage_engine.py#134-716) to broadcast RAW opportunities directly to a Redis channel (`arbex.raw_opps`).
* Extract the confidence/risk assessment logic into a standalone `SemanticEngine` microservice that listens to `arbex.raw_opps`, enriches the payload, and broadcasts back out to `arbex.scored_opps`.

### 2-B: Fast WebSocket Publishing
* Hook the FastAPI [WebSocketManager](file:///c:/Users/dhruv/OneDrive/Desktop/Capstone/backend/server/websocket_routes.py#24-439) straight into the `arbex.scored_opps` Redis channel, broadcasting directly to the Next.js frontend with near-zero latency.

## Phase 3: Identity & API Management
Transitioning from a synthetic terminal to a modular SaaS.

### 3-A: Supabase Architecture
* Integrate `@supabase/supabase-js` into Next.js for Google/Email Login.
* Create Database tables: `users`, `api_credentials`, `session_history`.
* Build the `/settings` dashboard in Next.js where users can toggle actual Broker APIs (e.g., Binance, OANDA) by saving their keys securely to Supabase.
