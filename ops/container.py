"""Small cron bootstrap; reads private configuration for every invocation."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import time
from nyaanews.config import Config
from nyaanews.util import atomic_json, now

os.environ["DATA_DIR"]="/data"
cfg_path="/run/nyaanews-config/runtime.env"

def scheduled():
    env=dict(os.environ,NYAANEWS_CONFIG=cfg_path,DATA_DIR="/data")
    result=subprocess.run([sys.executable,"-m","nyaanews.cli","--config",cfg_path,"run"],env=env)
    atomic_json(Path("/data/state/scheduler.json"),
                {"attempt":now().isoformat(),"exit_code":result.returncode})
    return result.returncode

def main():
    mode=sys.argv[1] if len(sys.argv)>1 else "daemon"
    if mode=="tick":
        return scheduled()
    if mode=="health":
        cfg=Config.load(cfg_path)
        cfg.prepare()
        pid=int(Path("/run/nyaanews-cron.pid").read_text())
        os.kill(pid,0)
        boot=json.loads(Path("/data/state/container.json").read_text())
        marker=Path("/data/state/scheduler.json")
        stamp=(json.loads(marker.read_text()).get("attempt") if marker.exists() else boot["started"])
        from nyaanews.util import parse_date
        result=json.loads(marker.read_text()).get("exit_code",0) if marker.exists() else 0
        # Exit 2 is a completed batch with isolated source/article failures.
        return 0 if result in (0,2) and (now()-parse_date(stamp)).total_seconds()<27*3600 else 1
    cfg=Config.load(cfg_path)
    cfg.prepare()
    tz=str(cfg.tz)
    zone=Path("/usr/share/zoneinfo")/tz
    if not zone.is_file():
        raise ValueError("Timezone data unavailable")
    Path("/etc/localtime").unlink(missing_ok=True)
    Path("/etc/localtime").symlink_to(zone)
    Path("/etc/timezone").write_text(tz+"\n")
    os.environ["TZ"]=tz
    time.tzset()
    cron=cfg.values.get("DAILY_CRON","0 8 * * *")
    if len(cron.split())!=5 or not re.fullmatch(r"[0-9*/,\- ]+",cron):
        raise ValueError("Unsafe cron expression")
    Path("/etc/cron.d/nyaanews").write_text(
        "SHELL=/bin/sh\nPATH=/usr/local/bin:/usr/bin:/bin\nPYTHONPATH=/app/src\nREQUIRE_MOUNT=1\n"
        +cron+" root /usr/local/bin/python /app/ops/container.py tick >> /proc/1/fd/1 2>> /proc/1/fd/2\n")
    Path("/etc/cron.d/nyaanews").chmod(0o644)
    ssh=Path("/tmp/nyaanews-ssh")
    ssh.mkdir(mode=0o700,exist_ok=True)
    key=Path("/run/nyaanews-config/ssh/id_ed25519")
    if key.exists():
        shutil.copyfile(key,ssh/"id_ed25519")
        (ssh/"id_ed25519").chmod(0o600)
    atomic_json(Path("/data/state/container.json"),{"started":now().isoformat(),"timezone":tz,"cron":cron})
    print(json.dumps({"service":"nyaanews","timezone":tz,"cron":cron,"message":"Nyaa be with you."}),flush=True)
    process=subprocess.Popen(["/usr/sbin/cron","-f"])
    Path("/run/nyaanews-cron.pid").write_text(str(process.pid))
    import signal
    def stop(*_):
        process.terminate()
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    return process.wait()

if __name__=="__main__":
    raise SystemExit(main())
