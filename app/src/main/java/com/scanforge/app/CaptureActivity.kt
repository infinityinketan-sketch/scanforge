package com.scanforge.app

import android.Manifest
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
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.app.ActivityCompat
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
    private lateinit var imageCapture: ImageCapture
    private lateinit var cameraExecutor: ExecutorService

    private val shots = mutableListOf<File>()
    private val handler = Handler(Looper.getMainLooper())
    private var capturing = false
    private var busy = false

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

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CAMERA), 42)
        } else {
            startCamera()
        }
    }

    private fun startCamera() {
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            val provider = providerFuture.get()
            val preview = Preview.Builder().build().also {
                it.surfaceProvider = previewView.surfaceProvider
            }
            imageCapture = ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
                .build()
            provider.unbindAll()
            provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, imageCapture)
        }, ContextCompat.getMainExecutor(this))
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 42 && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            startCamera()
        } else {
            Toast.makeText(this, "Camera permission required", Toast.LENGTH_LONG).show()
            finish()
        }
    }

    private fun toggle() {
        if (busy) return
        if (!capturing && shots.isNotEmpty()) { upload(); return }
        capturing = !capturing
        btn.text = if (capturing) "■ Stop & upload" else "▶ Start scanning"
        if (capturing) {
            handler.post(shotLoop)
        } else {
            handler.removeCallbacks(shotLoop)
            if (shots.size >= MIN_SHOTS) upload()
            else Toast.makeText(this, "Need at least $MIN_SHOTS photos", Toast.LENGTH_LONG).show()
        }
        updateUi()
    }

    private fun takeShot() {
        val f = File(cacheDir, "shot_${System.currentTimeMillis()}.jpg")
        imageCapture.takePicture(
            ImageCapture.OutputFileOptions.Builder(f).build(),
            cameraExecutor,
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(outputFileResults: ImageCapture.OutputFileResults) {
                    shots.add(f)
                    runOnUiThread { updateUi() }
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
        info.text = "${shots.size} photos · walk slowly around the object, 2–3 full circles, steady light"
    }

    private fun upload() {
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
                    Api.scan.uploadPhotos(job.job_id, parts)
                    Api.scan.process(job.job_id)
                    job.job_id
                }
                JobStore.add(this@CaptureActivity, LocalJob(id, System.currentTimeMillis(), "queued"))
                startActivity(
                    Intent(this@CaptureActivity, ResultActivity::class.java).putExtra("jobId", id)
                )
                finish()
            } catch (e: Exception) {
                Toast.makeText(this@CaptureActivity, "Upload failed: ${e.message}", Toast.LENGTH_LONG).show()
                busy = false
                btn.isEnabled = true
                btn.text = "▶ Start scanning"
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
    }
}
