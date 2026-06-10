"""
Kubernetes Operations — ArgoCD sync, KServe restart, namespace inspection.

The agent calls these tools AFTER Splunk confirms the diagnosis.
Principle: Splunk tells you WHAT is wrong. This module FIXES it.
"""
import os
import subprocess
import requests
from typing import Optional
from rich.console import Console

console = Console()

ARGOCD_SERVER = os.getenv("ARGOCD_SERVER", "localhost:8080")
ARGOCD_TOKEN  = os.getenv("ARGOCD_TOKEN", "")
DEMO_MODE     = os.getenv("DEMO_MODE", "false").lower() == "true"
KUBECONFIG    = os.getenv("KUBECONFIG", os.path.expanduser("~/.kube/config"))


def _kubectl(cmd: list[str], timeout: int = 30) -> tuple[str, str, int]:
    """Run a kubectl command. Returns (stdout, stderr, returncode)."""
    if DEMO_MODE:
        return _demo_kubectl(cmd)

    env = os.environ.copy()
    env["KUBECONFIG"] = KUBECONFIG
    result = subprocess.run(
        ["kubectl"] + cmd,
        capture_output=True, text=True, timeout=timeout, env=env
    )
    return result.stdout, result.stderr, result.returncode


def _demo_kubectl(cmd: list[str]) -> tuple[str, str, int]:
    """Simulate kubectl output for demo mode."""
    cmd_str = " ".join(cmd)
    if "get inferenceservice" in cmd_str:
        return (
            "NAME                         READY   URL                                        AGE\n"
            "neuroscale-bert-classifier   True    http://neuroscale-bert-classifier.default   2m\n"
            "neuroscale-sklearn-iris      True    http://neuroscale-sklearn-iris.default      18h\n",
            "", 0
        )
    elif "rollout restart" in cmd_str:
        return "deployment.apps/neuroscale-bert-classifier restarted\n", "", 0
    elif "patch" in cmd_str:
        return "inferenceservice.serving.kserve.io/neuroscale-bert-classifier patched\n", "", 0
    elif "get pods" in cmd_str:
        return (
            "NAME                                            READY   STATUS    RESTARTS\n"
            "neuroscale-bert-classifier-predictor-0-xxxxx   1/1     Running   0\n"
            "neuroscale-sklearn-iris-predictor-0-yyyyy       1/1     Running   0\n",
            "", 0
        )
    return f"# Demo: kubectl {' '.join(cmd)}\n", "", 0


# ── ArgoCD Operations ─────────────────────────────────────────────────────────

def argocd_sync(app_name: str, hard_refresh: bool = True) -> dict:
    """
    Trigger an ArgoCD hard refresh + sync for the specified application.

    This is the primary self-healing action: Splunk detects OutOfSync →
    agent calls this → model deployment is restored from Git.
    """
    console.print(f"[blue]ArgoCD sync:[/blue] triggering for '{app_name}'")

    if DEMO_MODE:
        return {
            "success": True,
            "app": app_name,
            "action": "sync_triggered",
            "message": f"[DEMO] ArgoCD sync triggered for {app_name}. Application will be Synced/Healthy within 60s.",
        }

    # Try ArgoCD API first
    if ARGOCD_TOKEN:
        result = _argocd_api_sync(app_name, hard_refresh)
        if result["success"]:
            return result

    # Fallback: kubectl annotation patch (works without ArgoCD CLI)
    stdout, stderr, rc = _kubectl([
        "-n", "argocd", "patch", "application", app_name,
        "--type", "merge",
        "-p", '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
    ])

    if rc == 0:
        return {
            "success": True,
            "app": app_name,
            "action": "hard_refresh_triggered",
            "message": f"ArgoCD hard refresh triggered for '{app_name}'. Sync will complete within 60s.",
            "stdout": stdout.strip(),
        }
    return {
        "success": False,
        "app": app_name,
        "error": stderr.strip() or "kubectl patch failed",
    }


def _argocd_api_sync(app_name: str, hard_refresh: bool) -> dict:
    """Sync via ArgoCD REST API."""
    try:
        headers = {"Authorization": f"Bearer {ARGOCD_TOKEN}"}
        # Hard refresh first
        if hard_refresh:
            requests.get(
                f"https://{ARGOCD_SERVER}/api/v1/applications/{app_name}?refresh=hard",
                headers=headers, verify=False, timeout=10
            )
        # Trigger sync
        resp = requests.post(
            f"https://{ARGOCD_SERVER}/api/v1/applications/{app_name}/sync",
            headers=headers,
            json={"prune": False, "dryRun": False},
            verify=False, timeout=15
        )
        return {
            "success": resp.status_code in (200, 201),
            "app": app_name,
            "action": "synced_via_api",
            "status_code": resp.status_code,
            "message": f"ArgoCD API sync triggered for '{app_name}'.",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_argocd_status() -> list[dict]:
    """Get status of all ArgoCD applications."""
    stdout, stderr, rc = _kubectl(["-n", "argocd", "get", "applications",
                                    "-o", "custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status"])
    if rc != 0:
        return [{"error": stderr.strip()}]

    lines = stdout.strip().split("\n")
    if len(lines) < 2:
        return []

    header = lines[0].split()
    results = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 3:
            results.append({"name": parts[0], "sync": parts[1], "health": parts[2]})
    return results


# ── KServe Operations ─────────────────────────────────────────────────────────

def restart_inference_service(name: str, namespace: str = "default") -> dict:
    """
    Restart a stuck KServe InferenceService by deleting its predictor pod.
    ArgoCD will recreate it from Git.
    """
    console.print(f"[blue]KServe restart:[/blue] '{name}' in namespace '{namespace}'")

    # Get predictor pod name
    stdout, _, rc = _kubectl([
        "-n", namespace, "get", "pods",
        "-l", f"serving.kserve.io/inferenceservice={name}",
        "-o", "jsonpath={.items[0].metadata.name}"
    ])

    if DEMO_MODE or (rc == 0 and stdout.strip()):
        pod_name = stdout.strip() or f"{name}-predictor-00001-deployment-xxx"

        # Delete the pod (Kubernetes will recreate from the ReplicaSet)
        del_stdout, del_stderr, del_rc = _kubectl([
            "-n", namespace, "delete", "pod", pod_name, "--grace-period=0"
        ])

        return {
            "success": DEMO_MODE or del_rc == 0,
            "action": "predictor_pod_restarted",
            "pod": pod_name,
            "inference_service": name,
            "namespace": namespace,
            "message": f"Predictor pod '{pod_name}' deleted. Kubernetes will recreate it in ~30s.",
        }

    return {
        "success": False,
        "error": f"No predictor pod found for InferenceService '{name}' in namespace '{namespace}'",
    }


def patch_inference_service_memory(name: str, namespace: str = "default",
                                    new_limit: str = "512Mi") -> dict:
    """Patch an InferenceService memory limit to resolve OOMKilled errors."""
    patch_json = (
        f'{{"spec":{{"predictor":{{"model":{{"resources":{{"limits":{{"memory":"{new_limit}"}}}}}}}}}}}}'
    )
    stdout, stderr, rc = _kubectl([
        "-n", namespace, "patch", "inferenceservice", name,
        "--type", "merge", "-p", patch_json
    ])

    if DEMO_MODE or rc == 0:
        return {
            "success": True,
            "action": "memory_limit_patched",
            "inference_service": name,
            "new_memory_limit": new_limit,
            "message": f"Memory limit for '{name}' updated to {new_limit}. "
                       f"Pod will restart automatically.",
        }
    return {"success": False, "error": stderr.strip()}


def get_inference_services(namespace: str = "default") -> list[dict]:
    """List all InferenceServices and their readiness."""
    stdout, stderr, rc = _kubectl([
        "-n", namespace, "get", "inferenceservices",
        "-o", "custom-columns=NAME:.metadata.name,READY:.status.modelStatus.states.activeModelState,URL:.status.url"
    ])
    if rc != 0:
        return [{"error": stderr.strip()}]

    lines = stdout.strip().split("\n")
    results = []
    for line in lines[1:]:
        parts = line.split()
        if parts:
            results.append({
                "name": parts[0],
                "ready": parts[1] if len(parts) > 1 else "Unknown",
                "url": parts[2] if len(parts) > 2 else "None",
            })
    return results


# ── OpenCost Operations ───────────────────────────────────────────────────────

def get_opencost_by_namespace(window: str = "6h") -> list[dict]:
    """
    Query OpenCost directly for namespace-level cost attribution.
    This is the FinOps intelligence layer — providing direct cost attribution per namespace.
    """
    opencost_url = os.getenv("OPENCOST_URL", "http://localhost:9090")

    if DEMO_MODE:
        return [
            {"namespace": "ml-team-b",  "totalCost": 0.8341, "cpuCost": 0.4102, "ramCost": 0.4239},
            {"namespace": "ml-team-a",  "totalCost": 0.1203, "cpuCost": 0.0601, "ramCost": 0.0602},
            {"namespace": "default",     "totalCost": 0.0441, "cpuCost": 0.0220, "ramCost": 0.0221},
            {"namespace": "kserve",      "totalCost": 0.0210, "cpuCost": 0.0105, "ramCost": 0.0105},
        ]

    try:
        resp = requests.get(
            f"{opencost_url}/model/allocation",
            params={"window": window, "aggregate": "namespace", "accumulate": "true"},
            timeout=10
        )
        data = resp.json()
        results = []
        for ns, info in (data.get("data") or [{}])[0].items():
            if ns == "__idle__":
                continue
            results.append({
                "namespace": ns,
                "totalCost": round(info.get("totalCost", 0), 4),
                "cpuCost": round(info.get("cpuCost", 0), 4),
                "ramCost": round(info.get("ramCost", 0), 4),
            })
        return sorted(results, key=lambda x: x["totalCost"], reverse=True)
    except Exception as exc:
        console.print(f"[yellow]OpenCost query warning:[/yellow] {exc}")
        return [{"error": str(exc)}]
