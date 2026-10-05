"""
Prepares this Flutter app for building: generates android/ if missing, then
installs the native streamer (CameraX + MJPEG server, see native/*.kt.tpl),
the manifest entries and Gradle dependencies. Safe to run repeatedly.

    python setup_android.py            # then: flutter build apk --release
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

PERMISSIONS = [
    "android.permission.CAMERA",
    "android.permission.INTERNET",
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.ACCESS_WIFI_STATE",
    "android.permission.WAKE_LOCK",
    "android.permission.FOREGROUND_SERVICE",
    "android.permission.FOREGROUND_SERVICE_CAMERA",
    "android.permission.POST_NOTIFICATIONS",
]
DEPS = [
    "androidx.camera:camera-core:1.3.4",
    "androidx.camera:camera-camera2:1.3.4",
    "androidx.camera:camera-lifecycle:1.3.4",
    "androidx.lifecycle:lifecycle-service:2.7.0",
]
MARK = "runner-cam-setup"


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def find_gradle():
    for n in ("android/app/build.gradle.kts", "android/app/build.gradle"):
        if os.path.exists(n):
            return n
    sys.exit("android/app/build.gradle(.kts) not found -- did flutter create fail?")


def main():
    if not os.path.isdir("android"):
        print("android/ missing -> flutter create ...")
        r = subprocess.run("flutter create --project-name camera_connector --org com.example --platforms android .",
                           shell=True)
        if r.returncode != 0:
            sys.exit("flutter create failed (is Flutter on PATH? run: flutter doctor)")

    gradle = find_gradle()
    g = read(gradle)

    m = re.search(r'namespace\s*=?\s*"([\w.]+)"', g) or re.search(r'applicationId\s*=?\s*"([\w.]+)"', g)
    package = m.group(1) if m else "com.example.camera_connector"
    print("package:", package)

    # --- Kotlin sources ---
    kdir = os.path.join("android", "app", "src", "main", "kotlin", *package.split("."))
    os.makedirs(kdir, exist_ok=True)
    jdir = os.path.join("android", "app", "src", "main", "java", *package.split("."))
    for stale in (os.path.join(jdir, "MainActivity.java"),):
        if os.path.exists(stale):
            os.remove(stale)
    for f in sorted(os.listdir("native")):
        if f.endswith(".kt.tpl"):
            write(os.path.join(kdir, f[:-4]), read(os.path.join("native", f)).replace("__PACKAGE__", package))
            print("installed", f[:-4])

    # --- manifest ---
    mpath = "android/app/src/main/AndroidManifest.xml"
    x = read(mpath)
    if MARK not in x:
        perms = "".join(f'    <uses-permission android:name="{p}"/>\n' for p in PERMISSIONS)
        x = x.replace("<application", f"    <!-- {MARK} -->\n{perms}    <application", 1)
        x = re.sub(r'android:label="[^"]*"', 'android:label="Runner Cam"', x, count=1)
        service = ('    <service android:name=".StreamService" android:exported="false"\n'
                   '        android:foregroundServiceType="camera"/>\n')
        x = x.replace("</application>", service + "    </application>", 1)
        print("manifest patched")
    if "usesCleartextTraffic" not in x:  # the app makes plain http:// calls to the PC (Find PC)
        x = x.replace("<application", '<application android:usesCleartextTraffic="true"', 1)
        print("manifest: cleartext http enabled")
    write(mpath, x)

    # --- gradle ---
    kts = gradle.endswith(".kts")
    if MARK not in g:
        g = re.sub(r"minSdk(Version)?\s*=?\s*(flutter\.minSdkVersion|\d+)",
                   "minSdk = 24" if kts else "minSdkVersion 24", g, count=1)
        block = "\n// %s\ndependencies {\n%s}\n" % (MARK, "".join(f'    implementation("{d}")\n' for d in DEPS))
        g += block
        write(gradle, g)
        print("gradle patched (minSdk 24 + CameraX)")

    print("\nSetup done. Next: flutter pub get && flutter build apk --release")


if __name__ == "__main__":
    main()
