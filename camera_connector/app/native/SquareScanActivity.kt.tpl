package __PACKAGE__

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.view.View
import android.widget.FrameLayout
import com.journeyapps.barcodescanner.DecoratedBarcodeView
import com.journeyapps.barcodescanner.Size
import com.journeyapps.barcodescanner.CaptureActivity

/** QR scanner with the same look as the inspection app: a perfect 250 dp square, crimson frame with corner brackets. */
class SquareScanActivity : CaptureActivity() {
    override fun initializeContent(): DecoratedBarcodeView {
        val view = DecoratedBarcodeView(this)
        val px = (250 * resources.displayMetrics.density).toInt()
        view.barcodeView.framingRectSize = Size(px, px)   // zxing reads only inside this square
        view.addView(FrameOverlay(this, px), FrameLayout.LayoutParams(-1, -1))
        return view
    }

    private class FrameOverlay(ctx: Context, private val side: Int) : View(ctx) {
        private val frame = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor("#DC143C"); style = Paint.Style.STROKE
            strokeWidth = 2f * ctx.resources.displayMetrics.density
        }
        private val corner = Paint(frame).apply { strokeWidth = 3f * ctx.resources.displayMetrics.density }

        override fun onDraw(c: Canvas) {
            val d = resources.displayMetrics.density
            val l = (width - side) / 2f; val t = (height - side) / 2f
            val r = l + side; val b = t + side; val n = 30f * d
            c.drawRect(l, t, r, b, frame)
            for ((x, y, dx, dy) in listOf(
                listOf(l, t, 1f, 1f), listOf(r, t, -1f, 1f), listOf(l, b, 1f, -1f), listOf(r, b, -1f, -1f)
            )) {
                c.drawLine(x, y, x + dx * n, y, corner)
                c.drawLine(x, y, x, y + dy * n, corner)
            }
        }
    }
}
