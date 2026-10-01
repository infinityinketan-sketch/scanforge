package com.scanforge.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.view.View

/** Circular shot-counter overlay shown on the camera preview. */
class RingView(context: Context) : View(context) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)

    var progress = 0
        set(value) { field = value; invalidate() }
    var total = 24
        set(value) { field = value; invalidate() }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val cx = width / 2f
        val cy = height / 2f - height * 0.08f
        val r = minOf(width, height) / 2f * 0.72f

        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 12f
        for (i in 0 until total) {
            val a0 = i * 360f / total
            paint.color = if (i < progress.coerceAtMost(total)) 0xFF00E676.toInt() else 0x44FFFFFF
            canvas.drawArc(cx - r, cy - r, cx + r, cy + r, a0 + 2f, 360f / total - 4f, false, paint)
        }

        paint.style = Paint.Style.FILL
        paint.textSize = 44f
        paint.color = 0xFFFFFFFF.toInt()
        val label = "$progress"
        canvas.drawText(label, cx - paint.measureText(label) / 2, cy + 16f, paint)
    }
}
