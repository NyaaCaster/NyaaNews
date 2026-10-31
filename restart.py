"""Production: validate storage, pull, restart and verify NyaaNews only."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent


def read_values(path):
    result = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip().strip("\"'")
    return result


def validate_storage(values):
    mount = Path(values["STORAGE_MOUNT_ROOT"]).resolve()
    if not os.path.ismount(mount):
        raise ValueError("Required production storage is not mounted")
    for key in ("HOST_DATA_DIR", "HOST_BACKUP_DIR"):
        path = Path(values[key]).resolve()
        if not path.is_dir() or mount not in path.parents:
            raise ValueError(key + " must exist inside the mounted storage")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-pull", action="store_true")
    parser.add_argument("--skip-cleanup", action="store_true")
    args = parser.parse_args()
    os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + "/snap/bin"
    config = ROOT / ".env/runtime.env"
    values = read_values(config)
    validate_storage(values)
    compose_file = ROOT / "docker-compose.publish.yml"
    if not compose_file.exists():
        compose_file = ROOT / "docker-compose.yml"
    command = ["docker", "compose", "--env-file", str(config), "-f", str(compose_file)]
    secrets = [values.get("PRIVATE_DOCKER_REGISTRY_HOST", ""), values.get("PRIVATE_DOCKER_REGISTRY_URL", "")]
    def run(args):
        with subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace") as process:
            for line in process.stdout:
                for secret in sorted(filter(None, secrets), key=len, reverse=True):
                    line = line.replace(secret, "<PRIVATE_REGISTRY>")
                print(line.rstrip(), flush=True)
            if process.wait():
                raise RuntimeError("Deployment command failed")
    run(command + ["config", "--quiet"])
    if not args.no_pull:
        run(command + ["pull"])
    run(command + ["down"])
    run(command + ["up", "-d", "--no-build"])
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        value = json.loads(subprocess.check_output(["docker", "inspect", "nyaanews"], text=True))[0]
        state = value["State"]
        if state.get("Health", {}).get("Status") == "healthy":
            break
        if state.get("Status") in ("exited", "dead"):
            raise RuntimeError("Container exited during deployment")
        time.sleep(3)
    else:
        raise RuntimeError("Container health did not pass before deadline")
    if not args.skip_cleanup:
        run(["docker", "image", "prune", "-f", "--filter", "label=project=nyaanews"])
    run(["docker", "ps", "--filter", "name=^/nyaanews$", "--format", "{{.Names}} {{.Status}}"])


if __name__ == "__main__":
    main()
