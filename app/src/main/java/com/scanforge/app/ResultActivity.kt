package com.scanforge.app

import android.app.AlertDialog
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
import org.json.JSONObject

class ResultActivity : ComponentActivity() {

    private lateinit var jobId: String
    private lateinit var statusText: TextView
    private lateinit var unlockBtn: Button
    private lateinit var payBtn: Button
    private var starting = false
    private var autoStartTried = false
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
        payBtn = Button(this).apply { text = "Pay & build my model" }
        glbBtn = Button(this).apply { text = "Download GLB (for Blender)" }
        stlBtn = Button(this).apply { text = "Download STL (for 3D printer)" }
        previewBtn = Button(this).apply { text = "View 3D preview" }
        root.addView(statusText)
        root.addView(payBtn)
        root.addView(unlockBtn)
        root.addView(glbBtn)
        root.addView(stlBtn)
        root.addView(previewBtn)
        setContentView(root)

        // Paid with points from the wallet; topping up happens on the Wallet screen.
        payBtn.setOnClickListener { startProcessing() }
        unlockBtn.setOnClickListener {
            unlockBtn.isEnabled = false
            val product = current?.export_product_id ?: BuildConfig.EXPORT_PRODUCT_ID
            BillingManager(this, jobId, product) { ok ->
                unlockBtn.isEnabled = true
                if (ok) {
                    toast("Purchase verified ✓")
                    refresh()
                }
                // Failures and cancellations are reported by BillingManager itself.
            }.launchExportUnlock()
        }
        glbBtn.setOnClickListener { download("glb") }
        stlBtn.setOnClickListener { download("stl") }
        previewBtn.setOnClickListener {
            current?.preview_url?.let { openPreview(it) }
        }

        poll()
    }

    private fun poll() = lifecycleScope.launch {
        runCatching { Account.ensure(this@ResultActivity) }
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

    override fun onResume() {
        super.onResume()
        refresh()   // e.g. back from the Wallet after buying points
    }

    /** Spend the scan's points and ask the server to start building the model. */
    private fun startProcessing() {
        if (starting) return
        starting = true
        payBtn.isEnabled = false
        lifecycleScope.launch {
            try {
                Account.ensure(this@ResultActivity)
                val r = withContext(Dispatchers.IO) { Api.scan.process(jobId, ProcessRequest(null)) }
                if (!r.isSuccessful) {
                    val detail = r.errorBody()?.string()?.let {
                        runCatching { JSONObject(it).getString("detail") }.getOrDefault(it)
                    }?.take(200) ?: "HTTP ${r.code()}"
                    if (r.code() == 402) askToBuyPoints(detail) else toast("Couldn't start: $detail")
                }
                refresh()
            } catch (e: Exception) {
                toast("Couldn't start: ${e.message}")
            } finally {
                starting = false
                payBtn.isEnabled = true
            }
        }
    }

    private fun askToBuyPoints(detail: String) {
        AlertDialog.Builder(this)
            .setTitle("Not enough points")
            .setMessage(detail)
            .setPositiveButton("Buy points") { _, _ ->
                startActivity(Intent(this, WalletActivity::class.java))
            }
            .setNegativeButton("Later", null)
            .show()
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
            "queued", "processing" -> {
                val pct = s.progress?.takeIf { it in 1..99 }?.let { " · $it%" } ?: ""
                when (s.tier) {
                    "basic" -> "⏳ Building your model…$pct\nUsually about 1 minute."
                    "quick" -> "⏳ Building your model…$pct\nUsually 1–3 minutes."
                    "hq" -> "⏳ Building your Premium model…$pct\n" +
                        "Usually 3–5 minutes. You can close the app and come back."
                    else -> "⏳ Reconstructing your model… (${s.n_photos} photos)\nThis can take a while."
                }
            }
            "failed" -> {
                // Only suggest retaking photos when the failure is about the photos, not the service.
                val photoProblem = listOf("photo", "overlap", "texture", "points", "match")
                    .any { s.error?.contains(it, ignoreCase = true) == true }
                val refunded = if (s.pay_before == true && s.paid && (s.points ?: 0) > 0)
                    "\n${s.points} points were returned to your wallet." else ""
                "❌ Failed: ${s.error}" + refunded +
                    if (photoProblem) "\nTip: retake photos with more overlap and steady lighting." else ""
            }
            "done" -> when {
                s.expired == true -> "🗑 This scan's files were deleted after 90 days, as our privacy notice says."
                s.paid -> "✅ Ready — download below"
                else -> "✅ Model ready! Unlock to export."
            }
            "created" -> when {
                s.pay_before == true && !s.paid ->
                    "📷 ${s.n_photos} photos uploaded.\nBuilding this model uses ${s.points ?: 0} points " +
                        "from your wallet; you can download it as soon as it's ready."
                else -> "Starting…"
            }
            else -> "Working…"
        }
        // Paid but not started (e.g. the app closed right after paying): start it now.
        if (s.status == "created" && s.pay_before == true && s.paid && !autoStartTried) {
            autoStartTried = true
            startProcessing()
        }
        payBtn.text = if ((s.points ?: 0) > 0) "Use ${s.points} points & build my model" else "Build my model"
        payBtn.visibility = if (s.status == "created" && s.pay_before == true && !s.paid) View.VISIBLE else View.GONE
        unlockBtn.visibility = if (s.status == "done" && !s.paid && s.pay_before != true) View.VISIBLE else View.GONE
        val paidReady = s.status == "done" && s.paid && s.expired != true
        glbBtn.visibility = if (paidReady) View.VISIBLE else View.GONE
        stlBtn.visibility = glbBtn.visibility
        previewBtn.visibility = if (s.status == "done" && s.expired != true) View.VISIBLE else View.GONE
    }

    private fun download(format: String) {
        val url = current?.download?.get(format) ?: return toast("No download link yet")
        lifecycleScope.launch {
            try {
                val dir = File(filesDir, "exports").apply { mkdirs() }
                val f = File(dir, "scan_${jobId}.${format}")
                // Stream to disk: full-resolution meshes can be tens of MB.
                withContext(Dispatchers.IO) {
                    Api.download(url, f)
                }
                toast("Saved ${f.name}")
                share(f, if (format == "glb") "model/gltf-binary" else "application/octet-stream")
            } catch (e: Exception) {
                toast("Download failed: ${e.message}")
            }
        }
    }

    /** Opens the GLB in Google's 3D viewer (Scene Viewer); falls back to the browser. */
    private fun openPreview(url: String) {
        val viewer = Uri.parse("https://arvr.google.com/scene-viewer/1.0").buildUpon()
            .appendQueryParameter("file", url)
            .appendQueryParameter("mode", "3d_only")
            .build()
        val intent = Intent(Intent.ACTION_VIEW, viewer).setPackage("com.google.android.googlequicksearchbox")
        try {
            startActivity(intent)
        } catch (_: Exception) {
            startActivity(Intent(Intent.ACTION_VIEW, viewer))
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
