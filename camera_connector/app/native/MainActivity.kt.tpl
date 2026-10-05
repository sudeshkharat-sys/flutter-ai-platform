package __PACKAGE__

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.google.zxing.integration.android.IntentIntegrator
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var pendingPermission: MethodChannel.Result? = null
    private var pendingQr: MethodChannel.Result? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "camera_connector")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "requestPermissions" -> requestPermissions(result)
                    "start" -> {
                        val i = Intent(this, StreamService::class.java)
                        for (k in listOf("port", "width", "height", "fps", "quality", "rotation"))
                            i.putExtra(k, call.argument<Int>(k) ?: 0)
                        i.putExtra("front", call.argument<Boolean>("front") ?: false)
                        i.putExtra("name", call.argument<String>("name") ?: "phone")
                        try {
                            ContextCompat.startForegroundService(this, i)
                            result.success(null)
                        } catch (e: Exception) {
                            DebugLog.e("startForegroundService failed", e)
                            result.error("start_failed", e.message, null)
                        }
                    }
                    "stop" -> {
                        startService(Intent(this, StreamService::class.java).setAction(StreamService.ACTION_STOP))
                        result.success(null)
                    }
                    "scanPc" -> {
                        val port = call.argument<Int>("port") ?: 8095
                        Thread {
                            val r = try { PcLink.scan(port) } catch (e: Exception) {
                                DebugLog.e("scan crashed", e); "[]"
                            }
                            runOnUiThread { result.success(r) }
                        }.start()
                    }
                    "scanQr" -> {
                        if (StreamService.instance != null) {
                            // the streaming service owns the camera; opening the scanner would steal it
                            result.error("busy", "Stop streaming first: the camera is in use", null)
                        } else {
                            pendingQr = result
                            try {
                                IntentIntegrator(this)
                                    .setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
                                    .setPrompt("Scan the QR code shown on the PC connector")
                                    .setBeepEnabled(false)
                                    .setOrientationLocked(false)
                                    .initiateScan()
                            } catch (e: Exception) {
                                DebugLog.e("QR scanner failed to open", e)
                                pendingQr = null
                                result.error("scan_failed", e.message, null)
                            }
                        }
                    }
                    "logLine" -> {
                        DebugLog.add(call.argument<String>("level") ?: "INFO", call.argument<String>("msg") ?: "")
                        result.success(null)
                    }
                    "registerPc" -> {
                        val ip = call.argument<String>("ip") ?: ""
                        val port = call.argument<Int>("port") ?: 8095
                        val phonePort = call.argument<Int>("phonePort") ?: 8080
                        val camName = call.argument<String>("name") ?: "phone"
                        Thread {
                            val r = try { PcLink.register(ip, port, phonePort, camName) } catch (e: Exception) {
                                DebugLog.e("register crashed", e); "error: ${e.message}"
                            }
                            runOnUiThread { result.success(r) }
                        }.start()
                    }
                    "status" -> result.success(StreamService.status())
                    "log" -> result.success(DebugLog.tail(300))
                    else -> result.notImplemented()
                }
            }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        val r = IntentIntegrator.parseActivityResult(requestCode, resultCode, data)
        if (r != null) {                       // result of the QR scanner
            DebugLog.i("QR scan finished: ${if (r.contents == null) "cancelled" else "got ${r.contents.length} chars"}")
            pendingQr?.success(r.contents ?: "")
            pendingQr = null
            return
        }
        super.onActivityResult(requestCode, resultCode, data)
    }

    private fun requestPermissions(result: MethodChannel.Result) {
        val needed = mutableListOf(Manifest.permission.CAMERA)
        if (Build.VERSION.SDK_INT >= 33) needed.add(Manifest.permission.POST_NOTIFICATIONS)
        val missing = needed.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) { result.success(true); return }
        pendingPermission = result
        ActivityCompat.requestPermissions(this, missing.toTypedArray(), 77)
    }

    override fun onRequestPermissionsResult(code: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(code, permissions, grantResults)
        if (code == 77) {
            // only the camera permission is mandatory; notifications are optional
            val camIdx = permissions.indexOf(Manifest.permission.CAMERA)
            val ok = camIdx < 0 || grantResults[camIdx] == PackageManager.PERMISSION_GRANTED
            DebugLog.i("permissions result: camera granted=$ok")
            pendingPermission?.success(ok)
            pendingPermission = null
        }
    }
}
