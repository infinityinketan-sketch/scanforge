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
import java.util.UUID

/**
 * Buys a point pack through Google Play and credits it to this install's account.
 *
 * Flow: connect → credit any pack paid for earlier but never credited → launch the Play
 * purchase sheet → the backend verifies the token and credits the points → consume it so the
 * pack can be bought again. The account id travels as the obfuscated account id, so the
 * backend can check the purchase belongs to this account.
 *
 * [onResult] gets the new balance, or null if nothing was credited.
 */
class PackBilling(
    private val activity: ComponentActivity,
    private val accountId: String,
    private val productId: String,
    private val onResult: (Int?) -> Unit,
) : PurchasesUpdatedListener {

    private val client: BillingClient = BillingClient.newBuilder(activity)
        .setListener(this)
        .enablePendingPurchases(PendingPurchasesParams.newBuilder().enableOneTimeProducts().build())
        .build()

    private var finished = false

    fun launch() {
        client.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) {
                if (result.responseCode != BillingClient.BillingResponseCode.OK) {
                    failOrTestBuild("Google Play billing unavailable (${result.debugMessage})")
                    return
                }
                creditOwnedThenBuy()
            }

            override fun onBillingServiceDisconnected() {
                if (!finished) fail("Billing service disconnected")
            }
        })
    }

    /** Packs paid for but never credited (app closed mid-way) block buying again: credit them first. */
    private fun creditOwnedThenBuy() {
        val params = QueryPurchasesParams.newBuilder().setProductType(BillingClient.ProductType.INAPP).build()
        client.queryPurchasesAsync(params) { _, owned ->
            activity.lifecycleScope.launch {
                for (p in owned) {
                    if (p.purchaseState == Purchase.PurchaseState.PURCHASED) creditAndConsume(p)
                }
                queryAndLaunch()
            }
        }
    }

    private fun queryAndLaunch() {
        val product = QueryProductDetailsParams.Product.newBuilder()
            .setProductId(productId)
            .setProductType(BillingClient.ProductType.INAPP)
            .build()
        val params = QueryProductDetailsParams.newBuilder().setProductList(listOf(product)).build()
        client.queryProductDetailsAsync(params) { result, details ->
            val pd = details.firstOrNull()
            if (result.responseCode != BillingClient.BillingResponseCode.OK || pd == null) {
                activity.runOnUiThread { failOrTestBuild("Point pack $productId not found in Play Console") }
                return@queryProductDetailsAsync
            }
            val flow = BillingFlowParams.newBuilder()
                .setProductDetailsParamsList(
                    listOf(BillingFlowParams.ProductDetailsParams.newBuilder().setProductDetails(pd).build())
                )
                .setObfuscatedAccountId(accountId)
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
                        val balance = creditAndConsume(p)
                        if (balance != null) done(balance) else fail("Could not add the points")
                    }
                    Purchase.PurchaseState.PENDING -> {
                        toast("Payment pending: points are added once it completes")
                        done(null)
                    }
                    else -> fail("Purchase not completed")
                }
            }
            BillingClient.BillingResponseCode.USER_CANCELED -> done(null)
            BillingClient.BillingResponseCode.ITEM_ALREADY_OWNED -> creditOwnedThenBuy()
            else -> fail("Purchase failed (${result.debugMessage})")
        }
    }

    /** Backend verifies and credits (once per token), then the purchase is consumed. */
    private suspend fun creditAndConsume(p: Purchase): Int? {
        val result = (
            try {
                withContext(Dispatchers.IO) {
                    Api.scan.buyPoints(PackPurchase(p.products.first(), p.purchaseToken))
                }
            } catch (_: Exception) {
                null
            }
        ) ?: return null
        val consume = ConsumeParams.newBuilder().setPurchaseToken(p.purchaseToken).build()
        client.consumeAsync(consume) { _, _ -> }
        return result.balance
    }

    /**
     * Sideloaded test builds can't use Play Billing. In debug builds only, ask the backend to
     * credit a test purchase; it does so only while ALLOW_DEV_BILLING=1 (never in production).
     */
    private fun failOrTestBuild(msg: String) {
        if (!BuildConfig.DEBUG) return fail(msg)
        activity.lifecycleScope.launch {
            val result = try {
                withContext(Dispatchers.IO) {
                    Api.scan.buyPoints(PackPurchase(productId, "test-build-${UUID.randomUUID()}"))
                }
            } catch (_: Exception) {
                null
            }
            if (result != null) {
                toast("Test build: payment skipped, ${result.credited} points added")
                done(result.balance)
            } else {
                fail(msg)
            }
        }
    }

    private fun fail(msg: String) {
        toast(msg)
        done(null)
    }

    private fun done(balance: Int?) {
        if (finished) return
        finished = true
        activity.runOnUiThread { onResult(balance) }
        // Leave the connection briefly open so a pending consume can complete.
        activity.window.decorView.postDelayed({ client.endConnection() }, 3000)
    }

    private fun toast(msg: String) = activity.runOnUiThread {
        Toast.makeText(activity, msg, Toast.LENGTH_LONG).show()
    }
}
