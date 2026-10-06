package com.scanforge.app

import android.Manifest
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
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
            withContext(Dispatchers.IO) { Api.scan.tiers().tiers }
        } catch (_: Exception) {
            emptyList()   // older server or offline: upload without choosing, server uses its default
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
            "moving to your right. 1–2 full circles, steady light. High-accuracy needs 20+ photos."
        if (!capturing && !busy) {
            btn.text = if (shots.size >= MIN_SHOTS) "⬆ Upload ${shots.size} photos" else "▶ Start scanning"
        }
    }

    /** Let the user pick Quick AI vs High-accuracy when the server offers more than one. */
    private fun chooseTierThenUpload() {
        if (tiers.size <= 1) return upload(tiers.firstOrNull()?.id)
        val labels = tiers.map { t ->
            val short = if (shots.size < t.min_photos) "\n⚠ needs ${t.min_photos}+ photos (you have ${shots.size})" else ""
            "${t.name}\n${t.detail}$short"
        }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle("How should we build your model?")
            .setItems(labels) { _, which ->
                val t = tiers[which]
                if (shots.size < t.min_photos) {
                    Toast.makeText(this, "Take at least ${t.min_photos} photos for ${t.name}", Toast.LENGTH_LONG).show()
                } else {
                    upload(t.id)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun upload(tier: String?) {
        busy = true
        btn.isEnabled = false
        btn.text = "Uploading…"
        lifecycleScope.launch {
            try {
                val id = withContext(Dispatchers.IO) {
                    val job = Api.scan.createJob()
                    val parts = shots.map {
                        MultipartBody.Part.createFormData(
                            "photos", it.name, it.asRequestBody("image/jpeg".toMediaType())
                        )
                    }
                    val up = Api.scan.uploadPhotos(job.job_id, parts)
                    if (!up.isSuccessful) error("upload rejected (HTTP ${up.code()})")
                    val proc = Api.scan.process(job.job_id, ProcessRequest(tier))
                    if (!proc.isSuccessful) {
                        error(proc.errorBody()?.string()?.take(200) ?: "HTTP ${proc.code()}")
                    }
                    job.job_id
                }
                JobStore.add(this@CaptureActivity, LocalJob(id, System.currentTimeMillis(), "queued"))
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
