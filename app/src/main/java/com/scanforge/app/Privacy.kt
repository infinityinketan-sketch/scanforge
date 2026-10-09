package com.scanforge.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.lifecycle.LifecycleCoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/** Consent to the privacy notice before the first scan, and "Delete my data". */
object Privacy {
    private var asking = false

    fun noticeUrl(wallet: Wallet?) =
        wallet?.privacy_url ?: (BuildConfig.API_BASE.trimEnd('/') + "/privacy")

    fun openNotice(activity: Activity, wallet: Wallet?) {
        activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(noticeUrl(wallet))))
    }

    /** Ask once per notice version. Declining closes the app: we can't scan without consent. */
    fun ensureConsent(activity: Activity, scope: LifecycleCoroutineScope, wallet: Wallet) {
        val version = wallet.privacy_version ?: return          // older server: nothing to ask
        if (wallet.consent_version == version || asking) return
        asking = true
        AlertDialog.Builder(activity)
            .setTitle("Your privacy")
            .setMessage(
                "To build your 3D model we upload the photos you take to our servers and to the " +
                    "3D processing services we use, some outside India.\n\n" +
                    "• Photos and models are deleted automatically after 90 days, or when you " +
                    "delete your data (Wallet → Delete my data).\n" +
                    "• We keep your points history. We don't ask for your name, email or phone number.\n" +
                    "• Please photograph objects only, not people or documents.\n\n" +
                    "Do you agree to the privacy notice?"
            )
            .setCancelable(false)
            .setPositiveButton("I agree") { _, _ ->
                scope.launch {
                    try {
                        val r = Account.call(activity) {
                            withContext(Dispatchers.IO) { Api.scan.consent(ConsentRequest(version)) }
                        }
                        if (!r.isSuccessful) error("HTTP ${r.code()}")
                    } catch (e: Exception) {
                        Toast.makeText(activity, "Couldn't save your choice: ${e.message}", Toast.LENGTH_LONG).show()
                    } finally {
                        asking = false
                    }
                }
            }
            .setNeutralButton("Read notice") { _, _ ->
                asking = false
                openNotice(activity, wallet)    // asked again when the app comes back
            }
            .setNegativeButton("Exit") { _, _ ->
                asking = false
                activity.finishAffinity()
            }
            .show()
    }

    /** Confirm, then erase everything on the server and on this phone. */
    fun confirmDelete(activity: Activity, scope: LifecycleCoroutineScope, balance: Int?, onDone: () -> Unit) {
        val points = if (balance != null && balance > 0)
            "\n\nYour $balance points will be lost. Points can't be refunded to cash." else ""
        AlertDialog.Builder(activity)
            .setTitle("Delete my data?")
            .setMessage(
                "This permanently deletes your photos and 3D models and closes your account.$points\n\n" +
                    "We keep only the payment records the law requires."
            )
            .setPositiveButton("Delete") { _, _ ->
                scope.launch {
                    try {
                        withContext(Dispatchers.IO) { Api.scan.deleteAccount() }
                        Account.forget(activity)
                        JobStore.save(activity, emptyList())
                        File(activity.filesDir, "exports").deleteRecursively()
                        Toast.makeText(activity, "Your data has been deleted", Toast.LENGTH_LONG).show()
                        onDone()
                    } catch (e: Exception) {
                        Toast.makeText(activity, "Couldn't delete: ${e.message}", Toast.LENGTH_LONG).show()
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
}
