package com.scanforge.app

import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.ConsumeParams
import com.android.billingclient.api.PendingPurchasesParams
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.PurchasesUpdatedListener
import com.android.billingclient.api.QueryProductDetailsParams
import com.android.billingclient.api.QueryPurchasesParams
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * One-time "export unlock" purchase, bought once per scan.
 *
 * Flow: connect → settle any purchases left over from an interrupted run → launch the
 * Play purchase sheet → verify the token with our backend → consume it so the same
 * product can be bought again for the next scan.
 *
 * The job id is attached as the obfuscated profile id, so the backend can check that a
 * purchase token belongs to the job it is being redeemed for.
 */
class BillingManager(
    private val activity: ComponentActivity,
    private val jobId: String,
    private val onResult: (Boolean) -> Unit,
) : PurchasesUpdatedListener {

    private val client: BillingClient = BillingClient.newBuilder(activity)
        .setListener(this)
        .enablePendingPurchases(PendingPurchasesParams.newBuilder().enableOneTimeProducts().build())
        .build()

    private var finished = false

    fun launchExportUnlock() {
        client.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) {
                if (result.responseCode != BillingClient.BillingResponseCode.OK) {
                    fail("Google Play billing unavailable (${result.debugMessage})")
                    return
                }
                settleOwnedThenBuy()
            }

            override fun onBillingServiceDisconnected() {
                if (!finished) fail("Billing service disconnected")
            }
        })
    }

    /** A purchase that was paid but never verified/consumed blocks buying again; finish those first. */
    private fun settleOwnedThenBuy() {
        val params = QueryPurchasesParams.newBuilder()
            .setProductType(BillingClient.ProductType.INAPP)
            .build()
        client.queryPurchasesAsync(params) { _, owned ->
            activity.lifecycleScope.launch {
                var unlockedThisJob = false
                for (p in owned) {
                    if (p.purchaseState != Purchase.PurchaseState.PURCHASED) continue
                    val owner = p.accountIdentifiers?.obfuscatedProfileId ?: jobId
                    if (verifyAndConsume(owner, p) && owner == jobId) unlockedThisJob = true
                }
                if (unlockedThisJob) succeed() else queryAndLaunch()
            }
        }
    }

    private fun queryAndLaunch() {
        val product = QueryProductDetailsParams.Product.newBuilder()
            .setProductId(BuildConfig.EXPORT_PRODUCT_ID)
            .setProductType(BillingClient.ProductType.INAPP)
            .build()
        val params = QueryProductDetailsParams.newBuilder().setProductList(listOf(product)).build()
        client.queryProductDetailsAsync(params) { result, details ->
            val pd = details.firstOrNull()
            if (result.responseCode != BillingClient.BillingResponseCode.OK || pd == null) {
                activity.runOnUiThread { fail("Export product not found in Play Console") }
                return@queryProductDetailsAsync
            }
            val flow = BillingFlowParams.newBuilder()
                .setProductDetailsParamsList(
                    listOf(BillingFlowParams.ProductDetailsParams.newBuilder().setProductDetails(pd).build())
                )
                .setObfuscatedProfileId(jobId)
                .build()
            activity.runOnUiThread { client.launchBillingFlow(activity, flow) }
        }
    }

    override fun onPurchasesUpdated(result: BillingResult, purchases: MutableList<Purchase>?) {
        when (result.responseCode) {
            BillingClient.BillingResponseCode.OK -> {
                val p = purchases?.firstOrNull() ?: return fail("No purchase returned")
                when (p.purchaseState) {
                    Purchase.PurchaseState.PURCHASED -> activity.lifecycleScope.launch {
                        if (verifyAndConsume(jobId, p)) succeed() else fail("Could not verify purchase")
                    }
                    Purchase.PurchaseState.PENDING -> {
                        toast("Payment pending — export unlocks once it completes")
                        done(false)
                    }
                    else -> fail("Purchase not completed")
                }
            }
            BillingClient.BillingResponseCode.USER_CANCELED -> done(false)
            BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED -> settleOwnedThenBuy()
            else -> fail("Purchase failed (${result.debugMessage})")
        }
    }

    /** Ask the backend to verify, then consume so the product can be bought for the next scan. */
    private suspend fun verifyAndConsume(forJob: String, p: Purchase): Boolean {
        val paid = try {
            withContext(Dispatchers.IO) {
                Api.scan.verify(VerifyRequest(forJob, p.products.first(), p.purchaseToken)).paid
            }
        } catch (_: Exception) {
            false
        }
        if (paid) {
            val consume = ConsumeParams.newBuilder().setPurchaseToken(p.purchaseToken).build()
            client.consumeAsync(consume) { _, _ -> }
        }
        return paid
    }

    private fun succeed() = done(true)

    private fun fail(msg: String) {
        toast(msg)
        done(false)
    }

    private fun done(ok: Boolean) {
        if (finished) return
        finished = true
        activity.runOnUiThread { onResult(ok) }
        // Leave the connection briefly open so a pending consume can complete.
        activity.window.decorView.postDelayed({ client.endConnection() }, 3000)
    }

    private fun toast(msg: String) = activity.runOnUiThread {
        Toast.makeText(activity, msg, Toast.LENGTH_LONG).show()
    }
}
