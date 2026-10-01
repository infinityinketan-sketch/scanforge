package com.scanforge.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class ResultActivity : ComponentActivity() {

    private lateinit var jobId: String
    private lateinit var statusText: TextView
    private lateinit var unlockBtn: Button
    private lateinit var glbBtn: Button
    private lateinit var stlBtn: Button
    private lateinit var previewBtn: Button
    private var current: JobStatus? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        jobId = intent.getStringExtra("jobId") ?: run { finish(); return }

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 64, 48, 48)
        }
        statusText = TextView(this).apply { textSize = 16f }
        unlockBtn = Button(this).apply { text = "Unlock export · one-time purchase" }
        glbBtn = Button(this).apply { text = "Download GLB (for Blender)" }
        stlBtn = Button(this).apply { text = "Download STL (for 3D printer)" }
        previewBtn = Button(this).apply { text = "Open preview in browser" }
        root.addView(statusText)
        root.addView(unlockBtn)
        root.addView(glbBtn)
        root.addView(stlBtn)
        root.addView(previewBtn)
        setContentView(root)

        unlockBtn.setOnClickListener {
            BillingManager(this, jobId) { ok ->
                if (ok) {
                    toast("Purchase verified ✓")
                    refresh()
                } else {
                    toast("Purchase failed or cancelled")
                }
            }.launchExportUnlock()
        }
        glbBtn.setOnClickListener { download("glb") }
        stlBtn.setOnClickListener { download("stl") }
        previewBtn.setOnClickListener {
            current?.preview_url?.let {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(it)))
            }
        }

        poll()
    }

    private fun poll() = lifecycleScope.launch {
        while (true) {
            try {
                val s = Api.scan.status(jobId)
                current = s
                JobStore.updateStatus(this@ResultActivity, jobId, s.status)
                render(s)
                if (s.status == "done" || s.status == "failed") break
            } catch (e: Exception) {
                statusText.text = "Connection error: ${e.message}"
            }
            delay(5000)
        }
    }

    private fun refresh() = lifecycleScope.launch {
        try {
            val s = Api.scan.status(jobId)
            current = s
            render(s)
        } catch (_: Exception) {
        }
    }

    private fun render(s: JobStatus) {
        statusText.text = when (s.status) {
            "queued", "processing" ->
                "⏳ Reconstructing your model… (${s.n_photos} photos)\nUsually takes 5–15 minutes."
            "failed" ->
                "❌ Failed: ${s.error}\nTip: retake photos with more overlap and steady lighting."
            "done" -> if (s.paid) "✅ Ready — download below" else "✅ Model ready! Unlock to export."
            else -> "Working…"
        }
        unlockBtn.visibility = if (s.status == "done" && !s.paid) View.VISIBLE else View.GONE
        val paidReady = s.status == "done" && s.paid
        glbBtn.visibility = if (paidReady) View.VISIBLE else View.GONE
        stlBtn.visibility = glbBtn.visibility
        previewBtn.visibility = if (s.status == "done") View.VISIBLE else View.GONE
    }

    private fun download(format: String) {
        val url = current?.download?.get(format) ?: return toast("No download link yet")
        lifecycleScope.launch {
            try {
                val bytes = withContext(Dispatchers.IO) {
                    Api.scan.downloadFile(url).byteStream().readBytes()
                }
                val dir = File(filesDir, "exports").apply { mkdirs() }
                val f = File(dir, "scan_${jobId}.${format}")
                f.writeBytes(bytes)
                toast("Saved ${f.name}")
                share(f, if (format == "glb") "model/gltf-binary" else "application/octet-stream")
            } catch (e: Exception) {
                toast("Download failed: ${e.message}")
            }
        }
    }

    private fun share(f: File, mime: String) {
        val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", f)
        val send = Intent(Intent.ACTION_SEND).apply {
            type = mime
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        startActivity(Intent.createChooser(send, "Share 3D model"))
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
