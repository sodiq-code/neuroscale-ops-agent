"""
Kubernetes → Splunk HEC Forwarder

Continuously watches your k3d cluster and forwards events to Splunk in real-time:
  - KServe InferenceService status changes and error events
  - Kyverno admission denial events (policy violations)
  - ArgoCD application sync status changes
  - OpenCost metrics (namespace-level hourly cost)

Run this as a background process while the cluster is active.
All events land in the 'neuroscale' Splunk index with typed sourcetypes
that the agent's SPL queries are designed to query.

Usage:
    python splunk-integration/k8s_to_splunk.py

Environment variables (from .env):
    SPLUNK_HEC_TOKEN, SPLUNK_HOST, SPLUNK_HEC_PORT, SPLUNK_INDEX
    OPENCOST_URL, KUBECONFIG
"""
import os
import sys
import time
import json
import logging
import threading
import requests
from pathlib import Path
from datetime import datetime

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.splunk_client import send_to_hec
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("k8s-to-splunk")

OPENCOST_URL = os.getenv("OPENCOST_URL", "http://localhost:9090")
KUBECONFIG   = os.getenv("KUBECONFIG", os.path.expanduser("~/.kube/config"))
POLL_INTERVAL = int(os.getenv("FORWARDER_POLL_SECONDS", "30"))


# ── Kubernetes event watchers ─────────────────────────────────────────────────

def watch_kubernetes_events():
    """
    Watch all Kubernetes events and forward relevant ones to Splunk.
    Filters for KServe (InferenceService), Kyverno (PolicyViolation), and ArgoCD.
    """
    try:
        from kubernetes import client, watch as k8s_watch, config as k8s_config
        try:
            k8s_config.load_kube_config(config_file=KUBECONFIG)
        except Exception:
            k8s_config.load_incluster_config()

        v1 = client.CoreV1Api()
        w = k8s_watch.Watch()
        log.info("Starting Kubernetes event watcher...")

        for event in w.stream(v1.list_event_for_all_namespaces, timeout_seconds=0):
            obj = event.get("object")
            if not obj:
                continue

            involved = obj.involved_object
            kind = involved.kind or ""
            reason = obj.reason or ""
            msg = obj.message or ""
            event_type = obj.type or "Normal"

            # KServe InferenceService events
            if kind == "InferenceService":
                send_to_hec({
                    "namespace": involved.namespace,
                    "name": involved.name,
                    "reason": reason,
                    "message": msg,
                    "type": event_type,
                    "count": obj.count or 1,
                    "first_time": str(obj.first_timestamp),
                    "last_time": str(obj.last_timestamp),
                }, sourcetype="kserve:events")

                if event_type == "Warning":
                    log.info(f"KServe WARNING: {involved.name} — {reason}: {msg[:80]}")

            # Kyverno policy violations
            elif reason in ("PolicyViolation", "PolicyError", "PolicyApplied", "PolicyFailed"):
                send_to_hec({
                    "resource": involved.name,
                    "resource_kind": kind,
                    "namespace": involved.namespace,
                    "policy": _extract_policy_name(msg),
                    "action": "DENY" if "deny" in msg.lower() or "block" in msg.lower() else "AUDIT",
                    "message": msg,
                    "reason": reason,
                }, sourcetype="kyverno:violations")
                log.info(f"Kyverno: {reason} — {involved.name}: {msg[:80]}")

            # ArgoCD application events
            elif kind == "Application" or "argocd" in (involved.namespace or "").lower():
                send_to_hec({
                    "app_name": involved.name,
                    "namespace": involved.namespace,
                    "reason": reason,
                    "message": msg,
                    "event_type": event_type,
                }, sourcetype="argocd:events")

    except ImportError:
        log.warning("kubernetes Python package not installed. Skipping K8s event watch.")
        log.warning("Install with: pip install kubernetes")
    except Exception as exc:
        log.error(f"Kubernetes event watcher error: {exc}")
        time.sleep(10)


def _extract_policy_name(message: str) -> str:
    """Try to extract policy name from Kyverno message."""
    import re
    # Kyverno messages often contain policy name
    match = re.search(r"policy[:\s]+([a-z0-9\-]+)", message.lower())
    if match:
        return match.group(1)
    return "unknown-policy"


# ── ArgoCD status poller ──────────────────────────────────────────────────────

def poll_argocd_status():
    """Poll ArgoCD application statuses and send to Splunk every 30s."""
    try:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_kube_config(config_file=KUBECONFIG)
        except Exception:
            k8s_config.load_incluster_config()

        custom_api = client.CustomObjectsApi()
        log.info("Starting ArgoCD status poller...")

        while True:
            try:
                apps = custom_api.list_cluster_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    plural="applications",
                )
                for app in apps.get("items", []):
                    meta = app.get("metadata", {})
                    status = app.get("status", {})
                    sync = status.get("sync", {})
                    health = status.get("health", {})

                    send_to_hec({
                        "app_name": meta.get("name"),
                        "namespace": meta.get("namespace", "argocd"),
                        "sync_status": sync.get("status", "Unknown"),
                        "health_status": health.get("status", "Unknown"),
                        "message": health.get("message", ""),
                        "revision": sync.get("revision", "")[:8],
                    }, sourcetype="argocd:events")

            except Exception as exc:
                log.debug(f"ArgoCD poll error (may be normal): {exc}")

            time.sleep(POLL_INTERVAL)

    except ImportError:
        log.warning("kubernetes not installed, skipping ArgoCD poller")


# ── OpenCost metrics poller ───────────────────────────────────────────────────

def poll_opencost_metrics():
    """
    Poll OpenCost every 5 minutes and send namespace cost data to Splunk.
    This creates the FinOps intelligence layer unique to this project.
    """
    log.info("Starting OpenCost metrics poller...")

    while True:
        try:
            resp = requests.get(
                f"{OPENCOST_URL}/model/allocation",
                params={
                    "window": "1h",
                    "aggregate": "namespace",
                    "accumulate": "false",
                    "step": "1h",
                },
                timeout=15,
            )

            if resp.status_code == 200:
                data = resp.json()
                # data is list of time windows
                for window_data in (data.get("data") or []):
                    for ns, info in window_data.items():
                        if ns in ("__idle__", "__unmounted__"):
                            continue

                        event = {
                            "namespace": ns,
                            "hourly_cost": round(info.get("totalCost", 0), 6),
                            "cpu_cost": round(info.get("cpuCost", 0), 6),
                            "memory_cost": round(info.get("ramCost", 0), 6),
                            "cpu_cores": round(info.get("cpuCoreHours", 0), 4),
                            "memory_bytes": info.get("ramByteHours", 0),
                            "efficiency": round(info.get("totalEfficiency", 0), 3),
                        }
                        send_to_hec(event, sourcetype="opencost:metrics")

                log.info(f"OpenCost: forwarded {len(data.get('data', [{}])[0])} namespaces to Splunk")
            else:
                log.warning(f"OpenCost returned {resp.status_code}")

        except requests.exceptions.ConnectionError:
            log.debug("OpenCost not reachable (port-forward may be needed)")
        except Exception as exc:
            log.error(f"OpenCost poll error: {exc}")

        time.sleep(300)  # Every 5 minutes


# ── Healthcheck sender ────────────────────────────────────────────────────────

def send_forwarder_heartbeat():
    """Send periodic heartbeat to Splunk so we can monitor forwarder health."""
    while True:
        send_to_hec({
            "event_type": "forwarder_heartbeat",
            "pid": os.getpid(),
            "status": "running",
            "poll_interval": POLL_INTERVAL,
        }, sourcetype="neuroscale:forwarder")
        time.sleep(60)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("NeuroScale → Splunk HEC Forwarder starting")
    log.info(f"Splunk HEC: {os.getenv('SPLUNK_HOST')}:{os.getenv('SPLUNK_HEC_PORT', 8088)}")
    log.info(f"Index: {os.getenv('SPLUNK_INDEX', 'neuroscale')}")
    log.info(f"OpenCost: {OPENCOST_URL}")
    log.info("=" * 60)

    threads = [
        threading.Thread(target=watch_kubernetes_events, daemon=True, name="k8s-events"),
        threading.Thread(target=poll_argocd_status, daemon=True, name="argocd-poll"),
        threading.Thread(target=poll_opencost_metrics, daemon=True, name="opencost-poll"),
        threading.Thread(target=send_forwarder_heartbeat, daemon=True, name="heartbeat"),
    ]

    for t in threads:
        t.start()
        log.info(f"Thread started: {t.name}")

    log.info("All forwarder threads running. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Forwarder shutting down.")


if __name__ == "__main__":
    main()
