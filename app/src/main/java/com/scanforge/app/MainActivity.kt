package com.scanforge.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ListView

class MainActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

        val newScan = Button(this).apply { text = "＋ New scan" }
        val list = ListView(this)
        root.addView(newScan)
        root.addView(list, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)

        fun refresh() {
            val jobs = JobStore.load(this)
            list.adapter = ArrayAdapter(
                this, android.R.layout.simple_list_item_1,
                jobs.map { job ->
                    val label = when (job.lastStatus) {
                        "done" -> "ready"
                        "failed" -> "failed"
                        "created" -> "uploading"
                        else -> "processing"
                    }
                    "${job.id}  ·  $label"
                }
            )
        }

        newScan.setOnClickListener {
            startActivity(Intent(this, CaptureActivity::class.java))
        }
        list.setOnItemClickListener { _, _, pos, _ ->
            val job = JobStore.load(this)[pos]
            startActivity(Intent(this, ResultActivity::class.java).putExtra("jobId", job.id))
        }
        refresh()
    }
}
