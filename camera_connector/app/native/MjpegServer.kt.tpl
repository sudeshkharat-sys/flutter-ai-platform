package __PACKAGE__

import java.io.BufferedInputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

/**
 * Tiny HTTP server, IP-Webcam style:
 *   /video         endless multipart MJPEG (what cv2.VideoCapture / VLC / browsers read)
 *   /snapshot.jpg  latest frame
 *   /ping          {"app":"runner-cam-phone",...}  (PC scan looks for this)
 *   /status /log   JSON stats / debug text
 *   /              small viewer page
 * Frames are only encoded while somebody is watching (see wantFrames) -- that
 * is the main thing that keeps the phone cool.
 */
class MjpegServer(
    private val port: Int,
    private val statusJson: () -> String,
) {
    private var server: ServerSocket? = null
    @Volatile private var running = false

    private val lock = Object()
    @Volatile private var latest: ByteArray? = null
    @Volatile private var seq = 0L

    val clients = AtomicInteger(0)
    @Volatile private var lastDemand = 0L

    fun wantFrames(): Boolean =
        clients.get() > 0 || System.currentTimeMillis() - lastDemand < 3000

    fun publish(jpeg: ByteArray) {
        synchronized(lock) {
            latest = jpeg
            seq++
            lock.notifyAll()
        }
    }

    fun start() {
        val s = ServerSocket()
        s.reuseAddress = true
        s.bind(InetSocketAddress(port))
        server = s
        running = true
        DebugLog.i("HTTP server listening on port $port")
        thread(name = "mjpeg-accept", isDaemon = true) {
            while (running) {
                try {
                    val c = s.accept()
                    thread(name = "mjpeg-client", isDaemon = true) { handle(c) }
                } catch (e: Exception) {
                    if (running) DebugLog.e("accept failed", e)
                }
            }
        }
    }

    fun stop() {
        running = false
        try { server?.close() } catch (_: Exception) {}
        synchronized(lock) { lock.notifyAll() }
    }

    private fun waitForFrame(after: Long, timeoutMs: Long): Pair<Long, ByteArray?> {
        synchronized(lock) {
            if (seq == after && running) lock.wait(timeoutMs)
            return Pair(seq, latest)
        }
    }

    private fun handle(sock: Socket) {
        val remote = sock.inetAddress?.hostAddress ?: "?"
        try {
            sock.tcpNoDelay = true
            sock.soTimeout = 10_000
            val input = BufferedInputStream(sock.getInputStream())
            val requestLine = readLine(input) ?: return
            while (true) {                       // skip headers
                val h = readLine(input) ?: return
                if (h.isEmpty()) break
            }
            val path = requestLine.split(" ").getOrNull(1)?.substringBefore("?") ?: "/"
            val out = sock.getOutputStream()
            when (path) {
                "/video" -> streamVideo(out, remote)
                "/snapshot.jpg" -> snapshot(out)
                "/ping" -> respond(out, "application/json",
                    """{"app":"runner-cam-phone","version":"0.1.0","port":$port}""".toByteArray())
                "/status" -> respond(out, "application/json", statusJson().toByteArray())
                "/log" -> respond(out, "text/plain; charset=utf-8", DebugLog.tail(300).toByteArray())
                "/", "/index.html" -> respond(out, "text/html", INDEX.toByteArray())
                else -> respond(out, "text/plain", "not found".toByteArray(), "404 Not Found")
            }
        } catch (e: Exception) {
            // client closed the connection or timed out -- normal for streams
            DebugLog.i("client $remote done: ${e.javaClass.simpleName}")
        } finally {
            try { sock.close() } catch (_: Exception) {}
        }
    }

    private fun streamVideo(out: OutputStream, remote: String) {
        val n = clients.incrementAndGet()
        DebugLog.i("viewer connected: $remote (viewers now $n)")
        try {
            out.write(("HTTP/1.1 200 OK\r\n" +
                "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n" +
                "Cache-Control: no-store\r\nConnection: close\r\n\r\n").toByteArray())
            out.flush()
            var last = -1L
            while (running) {
                lastDemand = System.currentTimeMillis()
                val (s, frame) = waitForFrame(last, 1000)
                if (frame == null || s == last) continue
                last = s
                out.write("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.size}\r\n\r\n".toByteArray())
                out.write(frame)
                out.write("\r\n".toByteArray())
                out.flush()
            }
        } finally {
            DebugLog.i("viewer left: $remote (viewers now ${clients.decrementAndGet()})")
        }
    }

    private fun snapshot(out: OutputStream) {
        lastDemand = System.currentTimeMillis()
        val (_, frame) = waitForFrame(seq, 2000)
        if (frame == null) respond(out, "text/plain", "no frame yet".toByteArray(), "503 Service Unavailable")
        else respond(out, "image/jpeg", frame)
    }

    private fun respond(out: OutputStream, type: String, body: ByteArray, status: String = "200 OK") {
        out.write(("HTTP/1.1 $status\r\nContent-Type: $type\r\nContent-Length: ${body.size}\r\n" +
            "Cache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n").toByteArray())
        out.write(body)
        out.flush()
    }

    private fun readLine(i: BufferedInputStream): String? {
        val sb = StringBuilder()
        while (true) {
            val c = i.read()
            if (c < 0) return if (sb.isEmpty()) null else sb.toString()
            if (c == '\n'.code) return sb.toString().trimEnd('\r')
            sb.append(c.toChar())
            if (sb.length > 8192) return null
        }
    }

    companion object {
        private const val INDEX = "<!doctype html><meta name=viewport content='width=device-width'>" +
            "<title>Runner Cam phone</title><body style='margin:0;background:#000'>" +
            "<img src='/video' style='width:100%'><pre style='color:#9f9'><a href='/status'>status</a> " +
            "<a href='/log'>log</a></pre>"
    }
}
