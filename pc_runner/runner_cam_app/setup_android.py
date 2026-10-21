"""Generate android/ (if missing) and patch the manifest for Runner Cam. Safe to run repeatedly."""
import os, re, subprocess, sys

here = os.path.dirname(os.path.abspath(__file__))
os.chdir(here)
if not os.path.isdir("android"):
    subprocess.check_call("flutter create --project-name runner_cam --org com.example --platforms android .", shell=True)
m = os.path.join("android", "app", "src", "main", "AndroidManifest.xml")
s = open(m, encoding="utf-8").read()
perms = ["CAMERA", "INTERNET", "ACCESS_NETWORK_STATE"]
add = "".join(f'    <uses-permission android:name="android.permission.{p}"/>\n'
              for p in perms if f"android.permission.{p}" not in s)
if add:
    s = s.replace("<application", add + "    <application", 1)
if "usesCleartextTraffic" not in s:
    s = s.replace("<application", '<application android:usesCleartextTraffic="true"', 1)
s = re.sub(r'android:label="[^"]*"', 'android:label="Runner Cam"', s, count=1)
open(m, "w", encoding="utf-8").write(s)
print("manifest patched:", m)
