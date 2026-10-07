---
class: irreversible-effects
scope: cross-cutting
surfaces_at:
  - code
  - merge
applies_when: an agent can reach a target it cannot undo changes to
applies_when_not: the agent has no write access anywhere
---

# Irreversible effects

## TL;DR

Limit reach first, make the effect reversible second, ask a human last.

## Symptom

A deleted resource that cannot be restored.

## Examples

- INC-001: a case from class alpha.
- INC-004: a case from class beta.

## Mechanism

The agent's identity reaches more than the task needs.

## Where it surfaces

Code and merge.

## Protections

### Limit the reach

- **Source:** one operator's practice
- **Cost:** setup time
- **Breaks when:** the identity is shared

## Evidence

No study.
