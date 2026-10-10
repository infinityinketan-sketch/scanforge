package com.scanforge.app

import android.Manifest
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class CaptureActivity : ComponentActivity() {

    private lateinit var previewView: PreviewView
    private lateinit var ring: RingView
    private lateinit var info: TextView
    private lateinit var btn: Button
    private var imageCapture: ImageCapture? = null
    private lateinit var cameraExecutor: ExecutorService

    private val shots = mutableListOf<File>()
    private var tiers: List<Tier> = emptyList()   // processing options the server offers
    private var balance: Int? = null              // points available, if known
    private val handler = Handler(Looper.getMainLooper())
    private var capturing = false
    private var busy = false

    private val cameraPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) {
                startCamera()
            } else {
                Toast.makeText(this, "Camera permission required", Toast.LENGTH_LONG).show()
                finish()
            }
        }

    private val shotLoop = object : Runnable {
        override fun run() {
            if (!capturing) return
            takeShot()
            handler.postDelayed(this, 2500)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        cameraExecutor = Executors.newSingleThreadExecutor()

        val root = FrameLayout(this)
        previewView = PreviewView(this)
        ring = RingView(this)
        info = TextView(this).apply { setTextColor(0xFFFFFFFF.toInt()); textSize = 14f }
        btn = Button(this).apply { text = "▶ Start scanning" }

        root.addView(previewView)
        root.addView(ring)
        val bottom = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 16, 32, 48)
            addView(info)
            addView(btn)
        }
        root.addView(bottom, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM))
        setContentView(root)

        btn.setOnClickListener { toggle() }
        loadTiers()

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED
        ) {
            cameraPermission.launch(Manifest.permission.CAMERA)
        } else {
            startCamera()
        }
    }

    private fun loadTiers() = lifecycleScope.launch {
        tiers = try {
            Account.ensure(this@CaptureActivity)
            withContext(Dispatchers.IO) { Api.scan.tiers().tiers }
        } catch (_: Exception) {
            emptyList()   // older server or offline: upload without choosing, server uses its default
        }
        balance = try {
            Account.call(this@CaptureActivity) { withContext(Dispatchers.IO) { Api.scan.wallet().balance } }
        } catch (_: Exception) {
            null
        }
    }

    private fun startCamera() {
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            val provider = providerFuture.get()
            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(previewView.surfaceProvider)
            }
            val capture = ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
                .build()
            provider.unbindAll()
            provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, capture)
            imageCapture = capture
        }, ContextCompat.getMainExecutor(this))
    }

    private fun toggle() {
        if (busy) return
        if (imageCapture == null) {
            Toast.makeText(this, "Camera is still starting…", Toast.LENGTH_SHORT).show()
            return
        }
        if (!capturing && shots.size >= MIN_SHOTS) { chooseTierThenUpload(); return }
        capturing = !capturing
        btn.text = if (capturing) "■ Stop & upload" else "▶ Start scanning"
        if (capturing) {
            handler.post(shotLoop)
        } else {
            handler.removeCallbacks(shotLoop)
            if (shots.size >= MIN_SHOTS) chooseTierThenUpload()
            else Toast.makeText(this, "Need at least $MIN_SHOTS photos", Toast.LENGTH_LONG).show()
        }
        updateUi()
    }

    private fun takeShot() {
        val capture = imageCapture ?: return
        if (shots.size >= MAX_SHOTS) {
            Toast.makeText(this, "Reached $MAX_SHOTS photos", Toast.LENGTH_SHORT).show()
            toggle()
            return
        }
        val f = File(cacheDir, "shot_${System.currentTimeMillis()}.jpg")
        capture.takePicture(
            ImageCapture.OutputFileOptions.Builder(f).build(),
            cameraExecutor,
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(outputFileResults: ImageCapture.OutputFileResults) {
                    // Mutate the list only on the main thread; it is read there too.
                    runOnUiThread {
                        shots.add(f)
                        updateUi()
                    }
                }

                override fun onError(exception: ImageCaptureException) {
                    runOnUiThread {
                        Toast.makeText(this@CaptureActivity, "Shot failed", Toast.LENGTH_SHORT).show()
                    }
                }
            }
        )
    }

    private fun updateUi() {
        ring.progress = shots.size
        info.text = "${shots.size} photos · start facing the object's front, then walk slowly around it " +
            "moving to your right. 1–2 full circles, steady light."
        if (!capturing && !busy) {
            btn.text = if (shots.size >= MIN_SHOTS) "⬆ Upload ${shots.size} photos" else "▶ Start scanning"
        }
    }

    /** Let the user pick Basic / Standard / Premium when the server offers more than one. */
    private fun chooseTierThenUpload() {
        if (tiers.size <= 1) return upload(tiers.firstOrNull()?.id)
        val dp = resources.displayMetrics.density
        val list = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding((20 * dp).toInt(), (4 * dp).toInt(), (20 * dp).toInt(), (8 * dp).toInt())
        }
        val dialog = AlertDialog.Builder(this)
            .setTitle(balance?.let { "Choose quality · you have $it points" } ?: "Choose your model quality")
            .setView(ScrollView(this).apply { addView(list) })
            .setNegativeButton("Cancel", null)
            .create()
        tiers.forEach { t ->
            list.addView(tierCard(t, dp) {
                dialog.dismiss()
                upload(t.id)
            })
        }
        dialog.show()
    }

    /** One option as a tappable card: name + price, quality stars, time, description, button. */
    private fun tierCard(t: Tier, dp: Float, onPick: () -> Unit): View {
        val enough = shots.size >= t.min_photos
        val accent = if (enough) 0xFF1565C0.toInt() else 0xFF9E9E9E.toInt()
        val px = { v: Int -> (v * dp).toInt() }
        val wrap = LinearLayout.LayoutParams.WRAP_CONTENT
        val match = LinearLayout.LayoutParams.MATCH_PARENT

        val shape = GradientDrawable().apply {
            cornerRadius = 14 * dp
            setStroke(px(2), accent)
            setColor(if (enough) 0xFFF1F6FD.toInt() else 0xFFF4F4F4.toInt())
        }
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = RippleDrawable(ColorStateList.valueOf(0x401565C0), shape, null)
            setPadding(px(16), px(14), px(16), px(14))
            isClickable = true
            isFocusable = true
            layoutParams = LinearLayout.LayoutParams(match, wrap).apply { topMargin = px(12) }
        }

        // Name ........ price
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(TextView(this).apply {
            text = t.name
            textSize = 19f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(if (enough) 0xFF0D47A1.toInt() else 0xFF757575.toInt())
        }, LinearLayout.LayoutParams(0, wrap, 1f))
        if (t.points > 0) {
            header.addView(TextView(this).apply {
                text = "${t.points} pts"
                textSize = 19f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(accent)
            })
        }
        card.addView(header)

        // Quality ★★★★☆ · About 2 minutes
        if (t.quality > 0 || !t.eta.isNullOrBlank()) {
            val stars = "★".repeat(t.quality.coerceIn(0, 5)) + "☆".repeat(5 - t.quality.coerceIn(0, 5))
            card.addView(TextView(this).apply {
                text = listOfNotNull(
                    if (t.quality > 0) "Quality $stars" else null,
                    t.eta?.takeIf { it.isNotBlank() },
                ).joinToString("   ·   ")
                textSize = 14f
                setTextColor(if (enough) 0xFFE65100.toInt() else 0xFF9E9E9E.toInt())
                setPadding(0, px(4), 0, 0)
            })
        }

        card.addView(TextView(this).apply {
            text = t.detail
            textSize = 13f
            setTextColor(0xFF424242.toInt())
            setPadding(0, px(6), 0, 0)
        })
        if (!enough) {
            card.addView(TextView(this).apply {
                text = "Needs ${t.min_photos}+ photos (you have ${shots.size})"
                textSize = 13f
                setTextColor(0xFFC62828.toInt())
                setPadding(0, px(6), 0, 0)
            })
        }

        // Full-width button so it's obvious the card is a choice.
        card.addView(TextView(this).apply {
            text = when {
                !enough -> "Not available"
                t.points <= 0 -> "Choose ${t.name}"
                else -> "Choose ${t.name} · ${t.points} points"
            }
            textSize = 15f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(0xFFFFFFFF.toInt())
            gravity = Gravity.CENTER
            setPadding(px(12), px(10), px(12), px(10))
            background = GradientDrawable().apply {
                cornerRadius = 22 * dp
                setColor(accent)
            }
        }, LinearLayout.LayoutParams(match, wrap).apply { topMargin = px(12) })

        card.setOnClickListener {
            if (enough) {
                onPick()
            } else {
                Toast.makeText(this, "Take at least ${t.min_photos} photos for ${t.name}", Toast.LENGTH_LONG).show()
            }
        }
        return card
    }

    private fun upload(tier: String?) {
        busy = true
        btn.isEnabled = false
        btn.text = "Uploading…"
        lifecycleScope.launch {
            try {
                val id = withContext(Dispatchers.IO) {
                    val job = Api.scan.createJob(CreateRequest(tier))
                    uploadShots(job.job_id)
                    // With a chosen option the scan is paid for on the next screen, which then
                    // starts processing. Without one (server has no services) start right away.
                    if (tier == null) {
                        val proc = Api.scan.process(job.job_id, ProcessRequest(null))
                        if (!proc.isSuccessful) {
                            error(proc.errorBody()?.string()?.take(200) ?: "HTTP ${proc.code()}")
                        }
                    }
                    job.job_id
                }
                JobStore.add(this@CaptureActivity, LocalJob(id, System.currentTimeMillis(), if (tier == null) "queued" else "created"))
                shots.forEach { it.delete() }
                startActivity(
                    Intent(this@CaptureActivity, ResultActivity::class.java).putExtra("jobId", id)
                )
                finish()
            } catch (e: Exception) {
                Toast.makeText(this@CaptureActivity, "Upload failed: ${e.message}", Toast.LENGTH_LONG).show()
                busy = false
                btn.isEnabled = true
                updateUi() // photos are kept, so the user can retry the upload
            }
        }
    }

    /**
     * Straight to file storage when the server offers it (faster, and photos don't pass through
     * the server); otherwise one multipart upload to the API.
     */
    private suspend fun uploadShots(jobId: String) = coroutineScope {
        val links = Api.scan.uploadUrls(jobId, UploadUrlsRequest(shots.size))
        val direct = links.body()?.takeIf { links.isSuccessful && it.direct && it.urls.size == shots.size }
        if (direct == null) {
            if (links.code() == 403 || links.code() == 409 || links.code() == 413) {
                error("upload rejected (HTTP ${links.code()})")
            }
            val parts = shots.map {
                MultipartBody.Part.createFormData("photos", it.name, it.asRequestBody("image/jpeg".toMediaType()))
            }
            val up = Api.scan.uploadPhotos(jobId, parts)
            if (!up.isSuccessful) error("upload rejected (HTTP ${up.code()})")
            return@coroutineScope
        }
        val lanes = Semaphore(4)   // a few at a time: fast on Wi-Fi, gentle on mobile data
        shots.zip(direct.urls).map { (file, url) ->
            async(Dispatchers.IO) {
                lanes.withPermit {
                    var attempt = 0
                    while (true) {
                        try {
                            Api.putFile(url, file, direct.content_type); break
                        } catch (e: Exception) {
                            if (++attempt >= 3) throw e
                            delay(1000L * attempt)
                        }
                    }
                }
            }
        }.awaitAll()
        val done = Api.scan.photosComplete(jobId)
        if (done.n_photos < shots.size) error("only ${done.n_photos} of ${shots.size} photos arrived")
    }

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacks(shotLoop)
        cameraExecutor.shutdown()
    }

    companion object {
        const val MIN_SHOTS = 12
        const val MAX_SHOTS = 150
    }
}
