package __PACKAGE__

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.IntentFilter
import android.os.BatteryManager
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.Matrix
import android.net.wifi.WifiManager
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import android.util.Size
import android.view.Surface
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.concurrent.Executors
import kotlin.concurrent.thread

/**
 * Foreground service: CameraX frames -> JPEG -> MjpegServer, plus a UDP beacon
 * so the PC can find the phone. Runs with the screen off.
 */
class StreamService : LifecycleService() {

    private var server: MjpegServer? = null
    private var provider: ProcessCameraProvider? = null
    @Volatile private var camera: androidx.camera.core.Camera? = null
    @Volatile private var torchOn = false
    @Volatile private var zoomRatio = 1f
    @Volatile private var autofocusOn = false
    @Volatile private var focusLoop = false
    private val analysisExecutor = Executors.newSingleThreadExecutor()
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    @Volatile private var beaconRunning = false

    // config
    private var port = 8080
    private var width = 1280
    private var height = 720
    private var maxFps = 20
    private var quality = 70
    private var rotation = Surface.ROTATION_0
    private var front = false
    private var name = "phone"
    @Volatile private var stopOnScreenOff = false
    private var screenReceiver: BroadcastReceiver? = null

    // stats
    @Volatile private var encoded = 0L
    @Volatile private var skipped = 0L
    @Volatile private var fps = 0.0
    @Volatile private var lastEncodeMs = 0.0
    @Volatile private var frameSize = ""
    @Volatile private var lastError = ""
    @Volatile private var thermal = "n/a"
    @Volatile private var lastCameraFrameAt = 0L
    @Volatile private var watchdogRunning = false
    private var thermalListener: PowerManager.OnThermalStatusChangedListener? = null
    private var fpsCount = 0
    private var fpsT = SystemClock.elapsedRealtime()
    private var nextDue = 0L
    private val jpegBuf = ByteArrayOutputStream(256 * 1024)

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_STOP) {
            shutdown()
            stopSelf()
            return START_NOT_STICKY
        }
        if (instance != null && server != null) {
            DebugLog.w("start requested but already running")
            return START_NOT_STICKY
        }
        port = intent?.getIntExtra("port", 8080) ?: 8080
        width = intent?.getIntExtra("width", 1280) ?: 1280
        height = intent?.getIntExtra("height", 720) ?: 720
        maxFps = intent?.getIntExtra("fps", 20) ?: 20
        quality = intent?.getIntExtra("quality", 70) ?: 70
        rotation = intent?.getIntExtra("rotation", Surface.ROTATION_0) ?: Surface.ROTATION_0
        front = intent?.getBooleanExtra("front", false) ?: false
        name = intent?.getStringExtra("name") ?: "phone"
        autofocusOn = intent?.getBooleanExtra("autofocus", false) ?: false
        stopOnScreenOff = intent?.getBooleanExtra("stopOnScreenOff", false) ?: false
        lastError = ""
        DebugLog.i("starting: ${width}x$height @max${maxFps}fps q$quality rot=$rotation front=$front port=$port")

        try {
            startForegroundCompat()
        } catch (e: Exception) {
            fail("startForeground failed (camera permission granted?)", e)
            return START_NOT_STICKY
        }
        try {
            acquireLocks()
            server = MjpegServer(port, name) { statusJson() }.also { it.start() }
            instance = this
            startBeacon()
            startWatchdog()
            watchThermal()
            watchScreen()
            bindCamera()
        } catch (e: Exception) {
            fail("start failed (port $port busy?)", e)
        }
        return START_NOT_STICKY
    }

    private fun fail(msg: String, e: Exception) {
        DebugLog.e(msg, e)
        lastError = "$msg: ${e.message}"
        shutdown()
        stopSelf()
    }

    private fun bindCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                val p = future.get()
                provider = p
                val selector = if (front) CameraSelector.DEFAULT_FRONT_CAMERA else CameraSelector.DEFAULT_BACK_CAMERA
                val aspect = if (width * 3 == height * 4)
                    AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY
                else AspectRatioStrategy.RATIO_16_9_FALLBACK_AUTO_STRATEGY
                val rs = ResolutionSelector.Builder()
                    .setAspectRatioStrategy(aspect)
                    .setResolutionStrategy(
                        ResolutionStrategy(Size(width, height), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER)
                    ).build()
                val analysis = ImageAnalysis.Builder()
                    .setResolutionSelector(rs)
                    .setTargetRotation(rotation)
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                    .build()
                analysis.setAnalyzer(analysisExecutor) { image ->
                    try {
                        val now = SystemClock.elapsedRealtime()
                        lastCameraFrameAt = now
                        val srv = server
                        val minGap = 1000L / maxFps.coerceAtLeast(1)
                        // Frame-rate cap that keeps its rhythm: a 30 fps camera with a 20 fps cap
                        // now gives 20 fps (a plain "gap since last frame" test gave 15).
                        if (srv == null || !srv.wantFrames() || now + 3 < nextDue) {
                            skipped++
                            return@setAnalyzer
                        }
                        nextDue = if (now - nextDue > minGap) now + minGap else nextDue + minGap
                        val bmp = image.toBitmap()
                        val rot = image.imageInfo.rotationDegrees
                        val upright = if (rot != 0) {
                            Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height,
                                Matrix().apply { postRotate(rot.toFloat()) }, true)
                                .also { bmp.recycle() }
                        } else bmp
                        jpegBuf.reset()
                        upright.compress(Bitmap.CompressFormat.JPEG, quality, jpegBuf)
                        frameSize = "${upright.width}x${upright.height}"
                        upright.recycle()
                        srv.publish(jpegBuf.toByteArray())
                        encoded++
                        lastEncodeMs = (SystemClock.elapsedRealtime() - now).toDouble()
                        fpsCount++
                        val t = SystemClock.elapsedRealtime()
                        if (t - fpsT >= 1000) {
                            fps = fpsCount * 1000.0 / (t - fpsT)
                            fpsCount = 0
                            fpsT = t
                        }
                    } catch (e: Exception) {
                        lastError = "frame error: ${e.message}"
                        DebugLog.e("frame error", e)
                    } finally {
                        image.close()
                    }
                }
                p.unbindAll()
                camera = p.bindToLifecycle(this, selector, analysis)
                applyTorchZoom()
                if (autofocusOn) { focusLoop = false; setAutofocus(true) }
                DebugLog.i("camera bound (${if (front) "front" else "back"})")
            } catch (e: Exception) {
                fail("camera bind failed", e)
            }
        }, ContextCompat.getMainExecutor(this))
    }

    // ---- torch / zoom (set from the app, re-applied after a camera rebind) ----

    private fun applyTorchZoom() {
        val c = camera ?: return
        try {
            if (c.cameraInfo.hasFlashUnit()) c.cameraControl.enableTorch(torchOn)
            val zs = c.cameraInfo.zoomState.value
            val r = zoomRatio.coerceIn(zs?.minZoomRatio ?: 1f, zs?.maxZoomRatio ?: 1f)
            c.cameraControl.setZoomRatio(r)
        } catch (e: Exception) {
            DebugLog.e("torch/zoom failed", e)
        }
    }

    fun setTorch(on: Boolean) {
        torchOn = on
        DebugLog.i("torch ${if (on) "ON" else "OFF"} (flash unit: ${camera?.cameraInfo?.hasFlashUnit()})")
        ContextCompat.getMainExecutor(this).execute { applyTorchZoom() }
    }

    fun setZoom(ratio: Float) {
        zoomRatio = ratio
        val zs = camera?.cameraInfo?.zoomState?.value
        DebugLog.i("zoom ${ratio}x requested (camera range ${zs?.minZoomRatio}..${zs?.maxZoomRatio})")
        ContextCompat.getMainExecutor(this).execute { applyTorchZoom() }
    }

    // ---- autofocus: on = re-focus on the frame centre every 8 s; off = the camera's own continuous AF ----

    private fun focusCenter() {
        val c = camera ?: return
        try {
            val f = androidx.camera.core.SurfaceOrientedMeteringPointFactory(1f, 1f).createPoint(0.5f, 0.5f)
            c.cameraControl.startFocusAndMetering(
                androidx.camera.core.FocusMeteringAction.Builder(f).build())
        } catch (e: Exception) {
            DebugLog.w("autofocus failed: ${e.message}")
        }
    }

    /** One focus on the frame centre, e.g. when a scan is armed after the camera moved from the label to the part. */
    fun focusNow() {
        ContextCompat.getMainExecutor(this).execute { focusCenter() }
    }

    fun setAutofocus(on: Boolean) {
        autofocusOn = on
        DebugLog.i("autofocus ${if (on) "ON (centre refocus every 8 s)" else "OFF (camera continuous AF)"}")
        if (!on) {
            ContextCompat.getMainExecutor(this).execute {
                try { camera?.cameraControl?.cancelFocusAndMetering() } catch (_: Exception) {}
            }
            return
        }
        if (focusLoop) return
        focusLoop = true
        thread(name = "autofocus", isDaemon = true) {
            while (autofocusOn && instance != null) {
                ContextCompat.getMainExecutor(this).execute { focusCenter() }
                Thread.sleep(8000)   // each refocus hunts for a moment and blurs frames: not too often
            }
            focusLoop = false
        }
    }

    // ---- beacon: tell the PC where we are ----

    private fun startBeacon() {
        beaconRunning = true
        thread(name = "beacon", isDaemon = true) {
            val sock = try { DatagramSocket().apply { broadcast = true } } catch (e: Exception) {
                DebugLog.w("beacon socket failed (PC can still scan): ${e.message}"); return@thread
            }
            while (beaconRunning) {
                try {
                    for (ni in NetworkInterface.getNetworkInterfaces()) {
                        if (!ni.isUp || ni.isLoopback) continue
                        for (ia in ni.interfaceAddresses) {
                            val addr = ia.address
                            val bc = ia.broadcast
                            if (addr !is Inet4Address || bc == null) continue
                            val msg = """{"app":"runner-cam-phone","ip":"${addr.hostAddress}","port":$port,"name":"$name"}""".toByteArray()
                            sock.send(DatagramPacket(msg, msg.size, bc, BEACON_PORT))
                        }
                    }
                } catch (_: Exception) {}
                Thread.sleep(2000)
            }
            sock.close()
        }
    }

    // ---- watchdog: tells "camera stopped delivering" apart from "network dropped" ----

    private fun startWatchdog() {
        watchdogRunning = true
        lastCameraFrameAt = SystemClock.elapsedRealtime()
        thread(name = "watchdog", isDaemon = true) {
            var stalled = false
            var lastViewers = -1
            var beat = 0
            while (watchdogRunning) {
                Thread.sleep(2000)
                val viewers = server?.clients?.get() ?: 0
                if (viewers != lastViewers) {
                    DebugLog.i(if (viewers > 0) "PC is pulling the video ($viewers viewer(s))" else "PC is NOT pulling the video (0 viewers) -- not connected, or the PC viewer closed")
                    lastViewers = viewers
                }
                if (++beat % 15 == 0) {   // every 30 s: heat, battery and stream health
                    DebugLog.i("health: ${batteryLine()} | thermal $thermal | ${Math.round(fps * 10) / 10.0} fps | encode ${Math.round(lastEncodeMs)} ms | $frameSize | viewers $viewers")
                }
                val age = SystemClock.elapsedRealtime() - lastCameraFrameAt
                if (age > 3000 && !stalled) {
                    stalled = true
                    DebugLog.w("CAMERA STALL: no frames from the camera for ${age / 1000}s (viewers=$viewers, thermal=$thermal) -- camera throttled/killed by the phone?")
                } else if (age <= 3000 && stalled) {
                    stalled = false
                    DebugLog.i("camera frames resumed")
                }
            }
        }
    }

    /** Screen switched off: either keep streaming (default) or stop, as set in the app. */
    private fun watchScreen() {
        val r = object : BroadcastReceiver() {
            override fun onReceive(c: Context?, i: Intent?) {
                if (stopOnScreenOff) {
                    DebugLog.w("screen off -- stopping the stream (setting: stop when screen is off)")
                    shutdown()
                    stopSelf()
                } else {
                    DebugLog.i("screen off -- camera keeps streaming (setting: keep streaming)")
                }
            }
        }
        screenReceiver = r
        try { registerReceiver(r, IntentFilter(Intent.ACTION_SCREEN_OFF)) } catch (e: Exception) { DebugLog.w("screen watch failed: ${e.message}") }
    }

    private fun batteryLine(): String {
        return try {
            val b = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            val level = b?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
            val scale = b?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
            val temp = (b?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) ?: 0) / 10.0
            val plugged = (b?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
            "battery ${if (level >= 0) level * 100 / scale else -1}%${if (plugged) " (charging)" else ""} ${temp}C"
        } catch (_: Exception) { "battery n/a" }
    }

    private fun thermalName(s: Int) = when (s) {
        0 -> "none"; 1 -> "light"; 2 -> "moderate"; 3 -> "SEVERE"; 4 -> "CRITICAL"; 5 -> "EMERGENCY"; 6 -> "SHUTDOWN"
        else -> "?"
    }

    private fun watchThermal() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            thermal = thermalName(pm.currentThermalStatus)
            DebugLog.i("thermal status: $thermal")
            val l = PowerManager.OnThermalStatusChangedListener { s ->
                thermal = thermalName(s)
                DebugLog.w("thermal status changed: $thermal")
            }
            thermalListener = l
            pm.addThermalStatusListener(l)
        } catch (e: Exception) {
            DebugLog.w("thermal status unavailable: ${e.message}")
        }
    }

    // ---- status ----

    /** Addresses a PC can use: Wi-Fi / hotspot only (not mobile data, 192.0.0.x CLAT or link-local). */
    private fun localIps(): List<String> {
        val all = mutableListOf<String>()
        val lan = mutableListOf<String>()
        try {
            for (ni in NetworkInterface.getNetworkInterfaces()) {
                if (!ni.isUp || ni.isLoopback) continue
                val mobile = ni.name.startsWith("rmnet") || ni.name.startsWith("ccmni") ||
                    ni.name.contains("clat") || ni.name.startsWith("dummy") || ni.name.startsWith("tun")
                for (a in ni.inetAddresses) {
                    if (a !is Inet4Address) continue
                    val ip = a.hostAddress ?: continue
                    all.add(ip)
                    if (!mobile && !ip.startsWith("192.0.0.") && !ip.startsWith("169.254.")) lan.add(ip)
                }
            }
        } catch (_: Exception) {}
        return if (lan.isNotEmpty()) lan else all
    }

    fun statusJson(): String = JSONObject().apply {
        put("running", server != null)
        put("port", port)
        put("ips", JSONArray(localIps()))
        put("viewers", server?.clients?.get() ?: 0)
        put("fps", Math.round(fps * 10) / 10.0)
        put("encoded", encoded)
        put("skipped", skipped)
        put("encodeMs", Math.round(lastEncodeMs * 10) / 10.0)
        put("size", frameSize)
        put("thermal", thermal)
        put("cameraAgeMs", if (lastCameraFrameAt == 0L) -1 else SystemClock.elapsedRealtime() - lastCameraFrameAt)
        put("error", lastError)
        put("torch", torchOn)
        put("zoom", Math.round(zoomRatio * 10) / 10.0)
        put("autofocus", autofocusOn)
    }.toString()

    // ---- foreground / locks / teardown ----

    private fun startForegroundCompat() {
        val mgr = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            mgr.createNotificationChannel(NotificationChannel(CHANNEL, "Camera streaming", NotificationManager.IMPORTANCE_LOW))
        }
        val open = PendingIntent.getActivity(this, 0,
            packageManager.getLaunchIntentForPackage(packageName),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n: Notification = NotificationCompat.Builder(this, CHANNEL)
            .setContentTitle("Runner Cam streaming")
            .setContentText("Camera is being served on port $port")
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA)
        } else {
            startForeground(1, n)
        }
    }

    @Suppress("DEPRECATION")
    private fun acquireLocks() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "runnercam:stream").apply { acquire() }
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
            WifiManager.WIFI_MODE_FULL_LOW_LATENCY else WifiManager.WIFI_MODE_FULL_HIGH_PERF
        wifiLock = wm.createWifiLock(mode, "runnercam:wifi").apply { acquire() }
    }

    private fun shutdown() {
        DebugLog.i("stopping")
        beaconRunning = false
        watchdogRunning = false
        try { screenReceiver?.let { unregisterReceiver(it) } } catch (_: Exception) {}
        screenReceiver = null
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            try {
                thermalListener?.let { (getSystemService(Context.POWER_SERVICE) as PowerManager).removeThermalStatusListener(it) }
            } catch (_: Exception) {}
            thermalListener = null
        }
        try { provider?.unbindAll() } catch (_: Exception) {}
        camera = null
        server?.stop()
        server = null
        try { wakeLock?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
        try { wifiLock?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
        instance = null
        fps = 0.0
    }

    override fun onDestroy() {
        shutdown()
        analysisExecutor.shutdown()
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "__PACKAGE__.STOP"
        private const val CHANNEL = "runnercam_stream"
        const val BEACON_PORT = 8092
        @Volatile var instance: StreamService? = null

        fun status(): String = instance?.statusJson()
            ?: """{"running":false,"error":""}"""
    }
}
