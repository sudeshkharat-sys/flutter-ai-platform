package __PACKAGE__

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var pendingPermission: MethodChannel.Result? = null

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
                    "status" -> result.success(StreamService.status())
                    "log" -> result.success(DebugLog.tail(300))
                    else -> result.notImplemented()
                }
            }
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
