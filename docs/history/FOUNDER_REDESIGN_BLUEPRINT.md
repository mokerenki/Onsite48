# Onsite48 Founder Redesign Blueprint

**Author:** Manus AI  
**Purpose:** A product and engineering redesign for turning Onsite48 from a collection of agent features into an autonomous operating system for specialised business services.

## Executive Position

If I had total authority, I would stop describing Onsite48 as a chatbot, a task runner, or a generic “AI workforce.” Those labels are too broad and lead directly to the current architectural mistakes: every feature becomes another tool, another route, another special planner shortcut, and another background process.

I would define Onsite48 as **the operational nervous system for a business**. Its job is to continuously observe the company, understand what matters, run delegated work, recommend or take bounded action, and show the owner what changed. The product would be closer to an **autonomous service platform** than a conversational interface. Conversation remains useful, but it becomes one way of issuing intent to the operating system—not the operating system itself.

> **Product thesis:** “Synth does not wait for prompts. It maintains a living model of your business, notices meaningful change, and executes the work you have delegated.”

This is where **Heartbeat** becomes the differentiator. A Manus-like execution engine is necessary, but it is not sufficient. The unique product is a persistent attention system that knows what to watch, what changed, why it matters, what has already been attempted, and which next action is justified.

## What I Would Stop Doing

The present design lets product capabilities leak into individual agent prompts, tool registries, runtime bootstraps, and page-level workflows. That creates an application in which the same agent behaves differently depending on whether it runs in a live session, a scheduled worker, an MCP flow, or a service page. I would stop adding functionality through one-off planner rules, isolated worker registrations, and page-specific orchestration.

I would also stop treating every scheduled run as a newly invented task. A recurring workflow is not merely a prompt plus a timestamp. It is an enduring operating commitment with an owner, objectives, sources, permissions, cost limits, expected cadence, escalation rules, and memory of past outcomes.

| Current anti-pattern | Founder decision | Replacement |
|---|---|---|
| Separate interactive and scheduled tool surfaces | Remove runtime divergence | One execution kernel and one capability resolver for every run type. |
| Prompt text as automation configuration | Eliminate prompt-only automation | Durable workflow definitions with typed inputs, policies, outcomes, and versioning. |
| Per-run approvals as the default automation model | Reserve approvals for exceptions | Explicit delegation tiers stored with the workflow. |
| MCP as a collection of wrappers | Promote MCP to infrastructure | A connector gateway with discovery, risk classification, policy, observability, and lifecycle management. |
| Department pages as disconnected feature screens | Make services operating domains | Every domain contributes signals, workflows, playbooks, and measurable outcomes to Heartbeat. |
| “Task completed” as success | Replace superficial completion | Measure business outcome, evidence quality, action reliability, cost, and owner trust. |

## The Target Product

Onsite48 should have three product surfaces, all backed by the same system.

| Surface | What the owner sees | Why it matters |
|---|---|---|
| **Mission Control** | A live company view: what changed, risks, opportunities, active work, pending decisions, and outcomes. | It proves Synth is awake even when the owner is absent. |
| **Service Teams** | Marketing, Sales, Finance, Product, and Executive Operations as specialised operating teams with playbooks and KPIs. | It makes the offering concrete and avoids a generic-agent experience. |
| **Run Workspace** | A transparent record of one autonomous run: goal, evidence, plan, tool calls, artifacts, decisions, budget, and final outcome. | It creates trust without forcing the owner into the loop. |

The chat interface becomes a command bar across these surfaces. The owner should be able to say, “Make our sales follow-up system more aggressive,” but the response should be a proposed or updated operating workflow—not merely a conversational answer.

## Target Architecture

The product should be designed around a single **Execution Kernel** with a distinct control plane. The agent must never choose capabilities, credentials, policy, persistence, or workflow ownership by itself. The model plans within a bounded execution contract; deterministic infrastructure decides what is available and what may run.

```mermaid
flowchart TB
    subgraph Product[Product Plane]
      MC[Mission Control]
      ST[Service Teams]
      RW[Run Workspace]
      CB[Command Bar]
    end

    subgraph Control[Control Plane]
      WF[Workflow Definitions]
      POL[Delegation & Policy Engine]
      CAP[Capability Catalog]
      CONN[Connector Accounts & MCP Manifests]
      BUD[Budgets, SLOs & Escalation Rules]
    end

    subgraph Engine[Execution Kernel]
      ORCH[Run Orchestrator]
      PLAN[Constrained Planner]
      DISP[Step Dispatcher]
      WORK[Worker Pools: API, Browser, Document, Model]
      VER[Evidence & Outcome Verifier]
    end

    subgraph Heartbeat[Heartbeat Intelligence]
      ING[Signal Ingestion]
      SG[Business Situation Graph]
      DET[Change Detection]
      NBA[Next-Best-Action Engine]
      MEM[Operational Memory]
    end

    subgraph Trust[Trust & Records]
      EV[Append-Only Run/Event Ledger]
      ART[Artifacts & Evidence]
      AUD[Audit Trail]
      OBS[Observability & Evaluation]
    end

    Product --> Control
    Product --> Heartbeat
    Heartbeat --> Control
    Control --> Engine
    Engine --> Trust
    Engine --> Heartbeat
    Trust --> Product