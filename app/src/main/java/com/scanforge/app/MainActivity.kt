package com.scanforge.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ListView
import java.text.DateFormat
import java.util.Date

class MainActivity : Activity() {

    private lateinit var list: ListView
    private var jobs: List<LocalJob> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

        val newScan = Button(this).apply { text = "＋ New scan" }
        list = ListView(this)
        root.addView(newScan)
        root.addView(list, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)

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
        jobs = JobStore.load(this).sortedByDescending { it.createdAt }
        val fmt = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
        list.adapter = ArrayAdapter(
            this, android.R.layout.simple_list_item_1,
            jobs.map { job ->
                val label = when (job.lastStatus) {
                    "done" -> "ready"
                    "failed" -> "failed"
                    "created" -> "uploading"
                    else -> "processing"
                }
                "Scan ${fmt.format(Date(job.createdAt))}  ·  $label"
            }
        )
    }
}
