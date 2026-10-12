# Onsite48 Agent Runtime Guide

**Audience:** engineers who need to understand the execution relationship between the Planner, ExecutorNode, ToolRegistry, Verifier, LangGraph, persistence, and the frontend.

**Scope:** this guide explains the runtime architecture in implementation terms. It maps the conceptual diagrams to the current code paths and shows what data moves between components.

> **Central rule:** the Planner decides what should be attempted, the ExecutorNode safely performs the next plan step, the ToolRegistry resolves the concrete capability, the Verifier evaluates the result, and LangGraph decides which node runs next.

---

## 1. The architecture in one sentence

Onsite48 executes a task by passing a shared checkpointed state through a LangGraph loop:

```text
Planner → ExecutorNode → Verifier → ExecutorNode or Planner or pause/fail/complete