package com.scanforge.app

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import retrofit2.HttpException

/**
 * The customer's points account. For now it is tied to this app install: created on first
 * use and kept in private app storage (uninstalling loses it). Google sign-in can later be
 * linked to the same account id without losing the balance.
 */
object Account {
    private const val PREFS = "account"
    private val lock = Mutex()

    /** Make sure this install has an account and its token is sent with API calls. */
    suspend fun ensure(ctx: Context): String = lock.withLock {
        Api.token?.let { return@withLock it }
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val saved = prefs.getString("token", null)
        val token = saved ?: withContext(Dispatchers.IO) { Api.scan.createAccount() }.also {
            prefs.edit().putString("token", it.token).putString("id", it.account_id).apply()
        }.token
        Api.token = token
        token
    }

    /**
     * Run [block] with the account; if the server no longer knows this install's token
     * (e.g. a test server was reset), start a fresh account and try once more.
     */
    suspend fun <T> call(ctx: Context, block: suspend () -> T): T {
        ensure(ctx)
        return try {
            block()
        } catch (e: HttpException) {
            if (e.code() != 401) throw e
            reset(ctx)
            ensure(ctx)
            block()
        }
    }

    private suspend fun reset(ctx: Context) = lock.withLock {
        Api.token = null
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()
    }

    fun id(ctx: Context): String? =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("id", null)
}
