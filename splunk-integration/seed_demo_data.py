"""
seed_demo_data.py — Push realistic NeuroScale demo events into Splunk HEC
Run this on your laptop to populate Splunk with demo data for the hackathon demo.
"""

import json
import time
import random
import requests
import urllib3
from datetime import datetime, timedelta

urllib3.disable_warnings()

HEC_URL = "https://localhost:8088/services/collector/event"
HEC_TOKEN = "b9212ff2-23e9-47e6-921d-a377227f8290"
INDEX = "neuroscale"

HEADERS = {
    "Authorization": f"Splunk {HEC_TOKEN}",
    "Content-Type": "application/json"
}

def send_event(event: dict, sourcetype: str):
    payload = {
        "index": INDEX,
        "sourcetype": sourcetype,
        "event": event,
        "time": time.time()
    }
    r = requests.post(HEC_URL, headers=HEADERS, json=payload, verify=False)
    if r.status_code == 200:
        print(f"  [OK] {sourcetype} — {list(event.keys())[:3]}")
    else:
        print(f"  [ERR] {r.status_code} — {r.text}")

def seed_model_metrics():
    print("\n[1/5] Seeding KServe model metrics...")
    models = ["llama-3-8b", "mistral-7b", "phi-3-mini", "gemma-2b"]
    for i in range(20):
        model = random.choice(models)
        # Simulate a spike then recovery for drama in the demo
        error_rate = 0.01 if i < 15 else random.uniform(0.08, 0.15)
        send_event({
            "name": model,
            "namespace": "neuroscale-models",
            "status": "Running" if error_rate < 0.05 else "Degraded",
            "latency_p99_ms": round(random.uniform(120, 450), 2),
            "requests_per_second": round(random.uniform(10, 150), 2),
            "error_rate": round(error_rate, 4),
            "gpu_utilization": round(random.uniform(0.4, 0.95), 2),
            "replicas_ready": 2 if error_rate < 0.05 else 0,
            "replicas_desired": 2
        }, "kserve:events")
        time.sleep(0.1)

def seed_cost_events():
    print("\n[2/5] Seeding OpenCost cost events...")
    namespaces = ["neuroscale-models", "neuroscale-ops", "monitoring", "argocd"]
    for i in range(15):
        ns = random.choice(namespaces)
        # Simulate cost spike mid-way
        base = 12.0 if ns == "neuroscale-models" else 3.0
        cost = base + (random.uniform(40, 65) if i == 10 else random.uniform(0, 5))
        send_event({
            "namespace": ns,
            "hourly_cost": round(cost, 4),
            "daily_cost_usd": round(cost * 24, 2),
            "cpu_cost": round(cost * 0.4, 4),
            "memory_cost": round(cost * 0.3, 4),
            "gpu_cost": round(cost * 0.3, 4),
            "cost_delta_pct": round((cost - base) / base * 100, 1),
            "alert": cost > 50
        }, "opencost:metrics")
        time.sleep(0.1)

def seed_policy_violations():
    print("\n[3/5] Seeding Kyverno policy violations...")
    policies = [
        ("require-resource-limits", "neuroscale-models", "llama-3-deployment", "high"),
        ("disallow-privileged-containers", "neuroscale-ops", "ops-agent-pod", "critical"),
        ("require-pod-probes", "monitoring", "prometheus-pod", "medium"),
        ("restrict-image-registries", "neuroscale-models", "mistral-deployment", "high"),
    ]
    for policy, ns, resource, severity in policies:
        send_event({
            "policy": policy,
            "namespace": ns,
            "resource": resource,
            "resource_kind": "Deployment",
            "action": "block",
            "severity": severity,
            "message": f"Policy {policy} violated by {resource} in {ns}",
            "remediation_available": True
        }, "kyverno:violations")
        time.sleep(0.2)

def seed_argocd_events():
    print("\n[4/5] Seeding ArgoCD sync events...")
    apps = ["neuroscale-models", "neuroscale-ops", "monitoring-stack", "ingress-nginx"]
    statuses = ["Synced", "Synced", "Synced", "OutOfSync", "Synced"]
    for app in apps:
        status = random.choice(statuses)
        send_event({
            "app_name": app,
            "sync_status": status,
            "health_status": "Healthy" if status == "Synced" else "Degraded",
            "revision": f"main@{random.randint(100000,999999):x}",
            "project": "neuroscale",
            "destination_namespace": app,
            "out_of_sync_resources": 0 if status == "Synced" else random.randint(1, 3)
        }, "argocd:events")
        time.sleep(0.2)

def seed_agent_actions():
    print("\n[5/5] Seeding Agent self-healing actions...")
    actions = [
        {
            "trigger": "model_error_rate_spike",
            "model": "llama-3-8b",
            "agent_reasoning": "Error rate exceeded 8% threshold. Runbook match: model-recovery-v2. Executing restart sequence.",
            "action_taken": "restart_inference_service",
            "result": "success",
            "recovery_time_seconds": 47,
            "confidence_score": 0.94
        },
        {
            "trigger": "cost_spike_detected",
            "namespace": "neuroscale-models",
            "agent_reasoning": "Hourly cost $58.20 exceeds $50 threshold. Identified idle GPU replicas. Scaling down from 3 to 1.",
            "action_taken": "scale_down_replicas",
            "result": "success",
            "cost_saved_usd": 34.80,
            "confidence_score": 0.91
        },
        {
            "trigger": "policy_violation_critical",
            "resource": "ops-agent-pod",
            "agent_reasoning": "Privileged container detected. Kyverno blocked deployment. Patching security context.",
            "action_taken": "patch_security_context",
            "result": "success",
            "policy": "disallow-privileged-containers",
            "confidence_score": 0.97
        }
    ]
    for action in actions:
        send_event(action, "neuroscale:agent")
        time.sleep(0.3)

if __name__ == "__main__":
    print("=" * 60)
    print("NeuroScale Demo Data Seeder")
    print("Pushing events to Splunk index: neuroscale")
    print("=" * 60)

    seed_model_metrics()
    seed_cost_events()
    seed_policy_violations()
    seed_argocd_events()
    seed_agent_actions()

    print("\n" + "=" * 60)
    print("Done! Check Splunk at http://localhost:8000")
    print("Search: index=neuroscale | stats count by sourcetype")
    print("=" * 60)
