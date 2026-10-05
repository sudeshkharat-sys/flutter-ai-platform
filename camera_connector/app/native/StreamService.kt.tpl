package __PACKAGE__

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
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

    // stats
    @Volatile private var encoded = 0L
    @Volatile private var skipped = 0L
    @Volatile private var fps = 0.0
    @Volatile private var lastEncodeMs = 0.0
    @Volatile private var frameSize = ""
    @Volatile private var lastError = ""
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
            server = MjpegServer(port) { statusJson() }.also { it.start() }
            instance = this
            startBeacon()
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
                p.bindToLifecycle(this, selector, analysis)
                DebugLog.i("camera bound (${if (front) "front" else "back"})")
            } catch (e: Exception) {
                fail("camera bind failed", e)
            }
        }, ContextCompat.getMainExecutor(this))
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
        put("error", lastError)
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
        try { provider?.unbindAll() } catch (_: Exception) {}
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
