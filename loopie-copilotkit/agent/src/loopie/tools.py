"""Simulated swarm tools."""

from __future__ import annotations

from typing import Any


def refund_tool(context: dict[str, Any]) -> dict[str, Any]:
    return {"tool": "refund_tool", "status": "simulated", "context": context}


def escalate_tool(context: dict[str, Any]) -> dict[str, Any]:
    return {"tool": "escalate_tool", "status": "simulated", "context": context}


def crm_lookup(context: dict[str, Any]) -> dict[str, Any]:
    return {"tool": "crm_lookup", "status": "simulated", "context": context}


def execute_tool(name: str, context: dict[str, Any]) -> dict[str, Any]:
    if name == "refund_tool":
        return refund_tool(context)
    if name == "escalate_tool":
        return escalate_tool(context)
    if name == "crm_lookup":
        return crm_lookup(context)
    return {"tool": name, "status": "unknown"}
