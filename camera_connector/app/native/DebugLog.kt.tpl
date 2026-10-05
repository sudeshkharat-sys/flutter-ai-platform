package __PACKAGE__

import android.util.Log
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Ring buffer of debug lines, shown in the app and served at http://phone:port/log. */
object DebugLog {
    private val lines = ArrayDeque<String>()
    private val fmt = SimpleDateFormat("HH:mm:ss.SSS", Locale.US)

    @Synchronized
    fun add(level: String, msg: String) {
        Log.println(if (level == "ERROR") Log.ERROR else Log.INFO, "RunnerCam", msg)
        lines.addLast("${fmt.format(Date())} [$level] $msg")
        while (lines.size > 500) lines.removeFirst()
    }

    fun i(msg: String) = add("INFO", msg)
    fun w(msg: String) = add("WARN", msg)
    fun e(msg: String, t: Throwable? = null) =
        add("ERROR", if (t == null) msg else "$msg: ${t.javaClass.simpleName}: ${t.message}")

    @Synchronized
    fun tail(n: Int): String = lines.toList().takeLast(n).joinToString("\n")
}
