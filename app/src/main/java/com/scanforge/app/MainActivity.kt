package com.scanforge.app

import android.content.Intent
import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ListView
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.DateFormat
import java.util.Date

class MainActivity : ComponentActivity() {

    private lateinit var list: ListView
    private lateinit var walletBtn: Button
    private var jobs: List<LocalJob> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

        walletBtn = Button(this).apply { text = "Wallet · … points" }
        val newScan = Button(this).apply { text = "＋ New scan" }
        list = ListView(this)
        root.addView(walletBtn)
        root.addView(newScan)
        root.addView(list, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)

        walletBtn.setOnClickListener {
            startActivity(Intent(this, WalletActivity::class.java))
        }
        newScan.setOnClickListener {
            startActivity(Intent(this, CaptureActivity::class.java))
        }
        list.setOnItemClickListener { _, _, pos, _ ->
            val job = jobs.getOrNull(pos) ?: return@setOnItemClickListener
            startActivity(Intent(this, ResultActivity::class.java).putExtra("jobId", job.id))
        }
    }

    // Refresh every time we come back, so statuses updated in ResultActivity show up.
    override fun onResume() {
        super.onResume()
        lifecycleScope.launch {
            walletBtn.text = try {
                val wallet = Account.call(this@MainActivity) {
                    withContext(Dispatchers.IO) { Api.scan.wallet() }
                }
                Privacy.ensureConsent(this@MainActivity, lifecycleScope, wallet)
                "Wallet · ${wallet.balance} points"
            } catch (_: Exception) {
                "Wallet (offline)"
            }
        }
        jobs = JobStore.load(this).sortedByDescending { it.createdAt }
        val fmt = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
        list.adapter = ArrayAdapter(
            this, android.R.layout.simple_list_item_1,
            jobs.map { job ->
                val label = when (job.lastStatus) {
                    "done" -> "ready"
                    "failed" -> "failed"
                    "created" -> "awaiting payment"
                    else -> "processing"
                }
                "Scan ${fmt.format(Date(job.createdAt))}  ·  $label"
            }
        )
    }
}
