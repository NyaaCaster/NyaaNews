"""Build and publish NyaaNews; never starts containers or transfers credentials."""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parent
IMAGE = "nyaanews"
ACCEPT = ", ".join(("application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json", "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json"))


def load_env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def mask(text, secrets):
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        text = text.replace(secret, "<PRIVATE_REGISTRY>")
    return text


def run(command, secrets, check=True):
    print("-> " + mask(" ".join(command), secrets), flush=True)
    with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace") as process:
        for line in process.stdout:
            print(mask(line.rstrip(), secrets), flush=True)
        code = process.wait()
    if code and check:
        raise RuntimeError("Command failed with exit code " + str(code))
    return code


def cleanup_plan(tags, keep, resolve):
    # Fail closed if either kept manifest cannot be resolved.
    protected = {resolve(tag) for tag in keep}
    if not protected or any(not value for value in protected):
        raise ValueError("Missing protected digest")
    obsolete = {}
    for tag in tags:
        if tag not in keep:
            digest = resolve(tag)
            if digest and digest not in protected:
                obsolete[digest] = tag
    return obsolete


def registry_cleanup(url, sha, secrets):
    opener = request.build_opener(request.ProxyHandler({}))
    base = url.rstrip("/") + "/v2/" + IMAGE
    try:
        with opener.open(base + "/tags/list", timeout=15) as response:
            tags = json.load(response).get("tags") or []
        def resolve(tag):
            req = request.Request(base + "/manifests/" + tag, method="HEAD",
                                  headers={"Accept": ACCEPT})
            with opener.open(req, timeout=15) as response:
                digest = response.headers.get("Docker-Content-Digest", "")
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                    raise ValueError("Invalid manifest digest")
                return digest
        for digest, tag in cleanup_plan(tags, {sha, "latest"}, resolve).items():
            req = request.Request(base + "/manifests/" + digest, method="DELETE")
            with opener.open(req, timeout=15) as response:
                if response.status not in (200, 202):
                    raise RuntimeError("Registry deletion failed")
            print("Removed obsolete manifest for " + tag)
    except Exception as exc:
        print("[WARN] Registry cleanup skipped: " + mask(str(exc), secrets))


def local_cleanup(host, sha, secrets):
    repo = host + "/" + IMAGE
    result = subprocess.run(["docker", "images", repo, "--format", "{{.Tag}}"],
                            capture_output=True, text=True)
    for tag in result.stdout.splitlines() if result.returncode == 0 else []:
        if tag in (sha, "latest", "<none>"):
            continue
        image = repo + ":" + tag
        used = subprocess.run(["docker", "ps", "-aq", "--filter", "ancestor=" + image],
                              capture_output=True, text=True)
        if used.returncode == 0 and not used.stdout.strip():
            run(["docker", "image", "rm", image], secrets, check=False)
    run(["docker", "image", "prune", "-f", "--filter", "label=project=nyaanews"],
        secrets, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / ".env/runtime.env"))
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--skip-push", action="store_true", help="Build nyaanews:local only")
    parser.add_argument("--skip-cleanup", action="store_true")
    args = parser.parse_args()
    values = load_env(args.config)
    host = values.get("PRIVATE_DOCKER_REGISTRY_HOST", "")
    url = values.get("PRIVATE_DOCKER_REGISTRY_URL") or ("http://" + host)
    if not args.skip_push and not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", host):
        raise ValueError("PRIVATE_DOCKER_REGISTRY_HOST is missing or invalid")
    secrets = [host, url]
    sha = subprocess.check_output(["git", "rev-parse", "--short=7", "HEAD"],
                                  cwd=ROOT, text=True).strip()
    tags = ["nyaanews:local"] if args.skip_push else [host + "/nyaanews:" + sha, host + "/nyaanews:latest"]
    command = ["docker", "build", "--label", "project=nyaanews"]
    if args.no_cache:
        command.append("--no-cache")
    for tag in tags:
        command += ["--tag", tag]
    run(command + ["."], secrets)
    if args.skip_push:
        return
    for tag in tags:
        for attempt in range(3):
            if run(["docker", "push", tag], secrets, check=False) == 0:
                break
            if attempt == 2:
                raise RuntimeError("Image push exhausted retries")
            time.sleep(2)
    if not args.skip_cleanup:
        registry_cleanup(url, sha, secrets)
        local_cleanup(host, sha, secrets)
    print("Published <PRIVATE_REGISTRY>/nyaanews:" + sha + " and :latest")


if __name__ == "__main__":
    main()
