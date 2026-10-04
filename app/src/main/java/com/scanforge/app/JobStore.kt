package com.scanforge.app

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

data class LocalJob(val id: String, val createdAt: Long, val lastStatus: String)

object JobStore {
    // Activities touch this from the main thread and coroutines; serialize file access.
    private val lock = Any()

    private fun file(ctx: Context) = File(ctx.filesDir, "jobs.json")

    fun load(ctx: Context): List<LocalJob> = synchronized(lock) {
        val f = file(ctx)
        if (!f.exists()) return emptyList()
        val arr = try { JSONArray(f.readText()) } catch (_: Exception) { return emptyList() }
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            LocalJob(o.getString("id"), o.getLong("createdAt"), o.optString("status", "queued"))
        }
    }

    fun save(ctx: Context, jobs: List<LocalJob>) = synchronized(lock) {
        val arr = JSONArray()
        jobs.forEach { job ->
            arr.put(
                JSONObject()
                    .put("id", job.id)
                    .put("createdAt", job.createdAt)
                    .put("status", job.lastStatus)
            )
        }
        file(ctx).writeText(arr.toString())
    }

    fun add(ctx: Context, job: LocalJob) = synchronized(lock) { save(ctx, load(ctx) + job) }

    fun updateStatus(ctx: Context, id: String, status: String) = synchronized(lock) {
        save(ctx, load(ctx).map { if (it.id == id) it.copy(lastStatus = status) else it })
    }
}
