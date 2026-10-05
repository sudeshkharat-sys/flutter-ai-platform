package __PACKAGE__

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.NetworkInterface
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Phone -> PC direction. scan() looks for the PC connector (GET /ping on its port,
 * answers {"app":"runner-cam-pc"}); register() tells the PC where this phone's stream is,
 * and returns the PC's own verdict on whether it can reach us (or why not).
 */
object PcLink {
    private fun ipToLong(a: ByteArray): Long =
        a.fold(0L) { acc, b -> (acc shl 8) or (b.toLong() and 0xff) }

    private fun longToIp(v: Long) = "${(v shr 24) and 255}.${(v shr 16) and 255}.${(v shr 8) and 255}.${v and 255}"

    fun localAddresses(): List<Pair<String, Int>> {
        val out = mutableListOf<Pair<String, Int>>()
        try {
            for (ni in NetworkInterface.getNetworkInterfaces()) {
                if (!ni.isUp || ni.isLoopback) continue
                for (ia in ni.interfaceAddresses) {
                    val a = ia.address
                    if (a is Inet4Address) out.add(Pair(a.hostAddress ?: continue, ia.networkPrefixLength.toInt()))
                }
            }
        } catch (e: Exception) {
            DebugLog.e("listing interfaces failed", e)
        }
        return out
    }

    /** Returns JSON array [{"ip","port","name"}]. Blocking: call from a background thread. */
    fun scan(port: Int): String {
        val found = JSONArray()
        val locals = localAddresses()
        if (locals.isEmpty()) {
            DebugLog.e("scan: phone has no IPv4 address -- Wi-Fi off?")
            return found.toString()
        }
        val hosts = linkedSetOf<String>()
        for ((ip, prefix) in locals) {
            val p = if (prefix in 22..30) prefix else 24            // cap at 1022 hosts
            val addr = ipToLong(java.net.InetAddress.getByName(ip).address)
            val mask = (0xFFFFFFFFL shl (32 - p)) and 0xFFFFFFFFL
            val net = addr and mask
            val count = (1L shl (32 - p)) - 2
            DebugLog.i("scan: interface $ip/$prefix -> probing ${count} hosts on port $port")
            for (i in 1..count) hosts.add(longToIp(net + i))
        }
        locals.forEach { hosts.remove(it.first) }
        val pool = Executors.newFixedThreadPool(64)
        val results = hosts.map { h -> pool.submit(java.util.concurrent.Callable<JSONObject?> { probe(h, port) }) }
        for (r in results) {
            try { r.get(30, TimeUnit.SECONDS)?.let { found.put(it) } } catch (_: Exception) {}
        }
        pool.shutdownNow()
        if (found.length() == 0) {
            DebugLog.w("scan: no PC answered on port $port. Is connector running on the PC (it prints " +
                "'viewer: ...')? Firewall popup allowed? Same Wi-Fi? Some office Wi-Fi blocks phone<->PC.")
        } else {
            DebugLog.i("scan: found ${found.length()} PC(s): $found")
        }
        return found.toString()
    }

    private fun probe(ip: String, port: Int): JSONObject? {
        var c: HttpURLConnection? = null
        return try {
            c = (URL("http://$ip:$port/ping").openConnection() as HttpURLConnection).apply {
                connectTimeout = 600; readTimeout = 800
            }
            val body = c.inputStream.bufferedReader().readText()
            val j = JSONObject(body)
            if (j.optString("app") == "runner-cam-pc") {
                JSONObject().put("ip", ip).put("port", j.optInt("port", port)).put("name", j.optString("name", ip))
            } else null
        } catch (_: Exception) {
            null
        } finally {
            c?.disconnect()
        }
    }

    /** Tell the PC where our stream is. Returns the PC's message, or our own error text. */
    fun register(pcIp: String, pcPort: Int, phonePort: Int): String {
        // use the phone address that sits closest to the PC's (same Wi-Fi interface)
        val mine = localAddresses().map { it.first }
            .maxByOrNull { ip -> ip.split(".").zip(pcIp.split(".")).takeWhile { (a, b) -> a == b }.size }
            ?: return "phone has no network address"
        val phone = "$mine:$phonePort"
        DebugLog.i("telling PC $pcIp:$pcPort that my stream is http://$phone/video")
        var c: HttpURLConnection? = null
        return try {
            c = (URL("http://$pcIp:$pcPort/api/register?phone=${URLEncoder.encode(phone, "UTF-8")}")
                .openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"; doOutput = true
                connectTimeout = 3000; readTimeout = 20000       // the PC runs its own checks first
                outputStream.close()
            }
            val body = c.inputStream.bufferedReader().readText()
            val msg = try { JSONObject(body).optString("message", body) } catch (_: Exception) { body }
            DebugLog.i("PC says: $msg")
            msg
        } catch (e: Exception) {
            val m = "could not reach the PC at $pcIp:$pcPort: ${e.javaClass.simpleName}: ${e.message}"
            DebugLog.e(m)
            m
        } finally {
            c?.disconnect()
        }
    }
}
