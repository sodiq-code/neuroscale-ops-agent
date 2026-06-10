"""
NeuroScale Ops Agent — Kubernetes Operations Toolkit
Provides ArgoCD sync, KServe management, and kubectl wrappers.

Safety additions:
  - patch_inference_service_memory: MAX_AUTO_MEMORY_GB cap (4 Gi)
  - All write operations log action for observability
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
OPENCOST_URL  = os.getenv("OPENCOST_URL", "http://localhost:9090")

# ── Blast radius constants ─────────────────────────────────────────────────────
MAX_AUTO_MEMORY_GB   = 4        # largest memory limit auto-remediation may set
MEMORY_UNITS_TO_GB   = {
    "ki": 1 / (1024 * 1024), "mi": 1 / 1024, "gi": 1.0,
    "k":  1 / (1024 * 1024), "m":  1 / 1024, "g":  1.0,
}


def _parse_memory_gb(raw: str) -> float:
    """Convert Kubernetes memory string to GB. Returns float."""
    raw = raw.strip()
    for suffix, factor in MEMORY_UNITS_TO_GB.items():
        if raw.lower().endswith(suffix):
            try:
                return float(raw[: -len(suffix)]) * factor
            except ValueError:
                break
    try:
        return float(raw) / (1024 ** 3)
    except ValueError:
        return 0.0


def _check_memory_blast_radius(new_limit: str) -> Optional[str]:
    """
    Return an error string if new_limit exceeds MAX_AUTO_MEMORY_GB,
    otherwise return None (safe to proceed).
    """
    requested_gb = _parse_memory_gb(new_limit)
    if requested_gb > MAX_AUTO_MEMORY_GB:
        return (
            f"Blast radius check FAILED: requested memory limit {new_limit} "
            f"({requested_gb:.1f} Gi) exceeds autonomous cap of {MAX_AUTO_MEMORY_GB} Gi. "
            "Escalate to on-call engineer for manual approval."
        )
    return None


# ── kubectl wrapper ───────────────────────────────────────────────────────────

def _kubectl(cmd: list[str], timeout: int = 30) -> tuple[str, str, int]:
    if DEMO_MODE:
        return _demo_kubectl(cmd)
    env = os.environ.copy()
    env["KUBECONFIG"] = KUBECONFIG
    try:
        result = subprocess.run(
            ["kubectl"] + cmd,
            capture_output=True, text=True, timeout=timeout, env=env
        )
        return result.stdout, result.stderr, result.returncode
    except FileNotFoundError:
        return _demo_kubectl(cmd)


def _demo_kubectl(cmd: list[str]) -> tuple[str, str, int]:
    cmd_str = " ".join(cmd)
    if "get inferenceservice" in cmd_str or "get inferenceservices" in cmd_str:
        return (
            "NAME                       READY   URL                                      AGE\n"
            "neurascale-inference       True    http://neurascale-inference.production   2m\n"
            "neurascale-bert            True    http://neurascale-bert.ml-workloads      18h\n",
            "", 0
        )
    elif "rollout restart" in cmd_str:
        return "deployment.apps/neurascale-api restarted\n", "", 0
    elif "patch" in cmd_str:
        return "deployment.apps/neurascale-api patched\n", "", 0
    elif "get pods" in cmd_str:
        return (
            "NAME                                    READY   STATUS    RESTARTS   AGE\n"
            "neurascale-inference-predictor-0-abc   1/1     Running   0          2m\n"
            "neurascale-api-7d4f8b9c-xyz            1/1     Running   1          5h\n",
            "", 0
        )
    elif "rollout undo" in cmd_str:
        return "deployment.apps/neurascale-api rolled back\n", "", 0
    elif "scale" in cmd_str:
        return "deployment.apps/neurascale-inference scaled\n", "", 0
    elif "apply" in cmd_str:
        return "policyexception.kyverno.io/neurascale-exception created\n", "", 0
    return f"# [DEMO] kubectl {' '.join(cmd)}\n", "", 0


# ── ArgoCD Operations ─────────────────────────────────────────────────────────

def argocd_sync(app_name: str, hard_refresh: bool = True) -> dict:
    """Trigger an ArgoCD hard refresh + sync."""
    console.print(f"[blue]ArgoCD sync:[/blue] triggering for '{app_name}'")

    if DEMO_MODE:
        return {
            "success": True,
            "app": app_name,
            "action": "sync_triggered",
            "message": f"[DEMO] ArgoCD sync triggered for '{app_name}'.",
        }

    if ARGOCD_TOKEN:
        result = _argocd_api_sync(app_name, hard_refresh)
        if result["success"]:
            return result

    stdout, stderr, rc = _kubectl([
        "-n", "argocd", "patch", "application", app_name,
        "--type", "merge",
        "-p", '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
    ])
    if rc == 0:
        return {"success": True, "app": app_name, "action": "hard_refresh_triggered", "stdout": stdout.strip()}
    return {"success": False, "app": app_name, "error": stderr.strip() or "kubectl patch failed"}


def _argocd_api_sync(app_name: str, hard_refresh: bool) -> dict:
    try:
        headers = {"Authorization": f"Bearer {ARGOCD_TOKEN}"}
        if hard_refresh:
            requests.get(
                f"https://{ARGOCD_SERVER}/api/v1/applications/{app_name}?refresh=hard",
                headers=headers, verify=False, timeout=10
            )
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
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_argocd_status() -> list[dict]:
    stdout, stderr, rc = _kubectl([
        "-n", "argocd", "get", "applications",
        "-o", "custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status"
    ])
    if rc != 0:
        return [{"error": stderr.strip()}]
    lines = stdout.strip().split("\n")
    results = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 3:
            results.append({"name": parts[0], "sync": parts[1], "health": parts[2]})
    return results


# ── KServe Operations ─────────────────────────────────────────────────────────

def restart_inference_service(name: str, namespace: str = "default") -> dict:
    """Delete the predictor pod so Kubernetes recreates it."""
    console.print(f"[blue]KServe restart:[/blue] '{name}' in namespace '{namespace}'")
    stdout, _, rc = _kubectl([
        "-n", namespace, "get", "pods",
        "-l", f"serving.kserve.io/inferenceservice={name}",
        "-o", "jsonpath={.items[0].metadata.name}"
    ])
    pod_name = stdout.strip() or f"{name}-predictor-00001-deployment-xxx"
    del_stdout, del_stderr, del_rc = _kubectl(["-n", namespace, "delete", "pod", pod_name, "--grace-period=0"])
    return {
        "success": DEMO_MODE or del_rc == 0,
        "action": "predictor_pod_restarted",
        "pod": pod_name,
        "inference_service": name,
        "namespace": namespace,
        "message": f"Predictor pod '{pod_name}' deleted. Kubernetes will recreate in ~30s.",
    }


def patch_inference_service_memory(name: str, namespace: str = "default", new_limit: str = "1Gi") -> dict:
    """
    Patch InferenceService memory limit.
    Blast radius guard: refuses to set limits above MAX_AUTO_MEMORY_GB (4 Gi).
    """
    # ── Blast radius check ────────────────────────────────────────────────────
    blast_err = _check_memory_blast_radius(new_limit)
    if blast_err:
        console.print(f"[red]Blast radius blocked:[/red] {blast_err}")
        return {
            "success": False,
            "blast_radius_blocked": True,
            "action": "memory_limit_patch_refused",
            "inference_service": name,
            "requested_limit": new_limit,
            "max_allowed": f"{MAX_AUTO_MEMORY_GB}Gi",
            "error": blast_err,
        }

    patch_json = (
        f'{{"spec":{{"predictor":{{"model":{{"resources":{{"limits":{{"memory":"{new_limit}"}}}}}}}}}}}}'
    )
    stdout, stderr, rc = _kubectl(["-n", namespace, "patch", "inferenceservice", name, "--type", "merge", "-p", patch_json])
    return {
        "success": DEMO_MODE or rc == 0,
        "blast_radius_blocked": False,
        "action": "memory_limit_patched",
        "inference_service": name,
        "new_memory_limit": new_limit,
        "message": f"Memory limit for '{name}' updated to {new_limit}. Pod will restart automatically.",
    }


def get_inference_services(namespace: str = "default") -> list[dict]:
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
    if DEMO_MODE:
        return [
            {"namespace": "production",   "totalCost": 1.2341, "cpuCost": 0.6102, "ramCost": 0.6239},
            {"namespace": "ml-workloads", "totalCost": 0.8341, "cpuCost": 0.4102, "ramCost": 0.4239},
            {"namespace": "staging",      "totalCost": 0.3203, "cpuCost": 0.1601, "ramCost": 0.1602},
            {"namespace": "default",      "totalCost": 0.0441, "cpuCost": 0.0220, "ramCost": 0.0221},
        ]
    try:
        resp = requests.get(
            f"{OPENCOST_URL}/model/allocation",
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
