package com.scanforge.app

import android.content.Intent
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.DateFormat
import java.util.Date
import kotlin.math.roundToInt

/** Points balance, point packs to buy, and every credit and debit. */
class WalletActivity : ComponentActivity() {

    private lateinit var balanceText: TextView
    private lateinit var costsText: TextView
    private lateinit var packsBox: LinearLayout
    private lateinit var historyBox: LinearLayout
    private var dp = 1f
    private var buying = false
    private var wallet: Wallet? = null
    private lateinit var accountText: TextView

    private val ink = 0xFF1B2330.toInt()
    private val muted = 0xFF5F6B7A.toInt()
    private val accent = 0xFF1565C0.toInt()
    private val credit = 0xFF1B7F45.toInt()
    private val debit = 0xFFC62828.toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        dp = resources.displayMetrics.density
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(20), px(24), px(20), px(32))
        }

        // Balance
        val balanceCard = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(20), px(18), px(20), px(18))
            background = rounded(0xFFEAF2FD.toInt(), stroke = accent)
        }
        balanceCard.addView(label("Your balance", 14f, muted))
        balanceText = label("…", 34f, ink, bold = true)
        balanceCard.addView(balanceText)
        costsText = label("", 13f, muted).apply { setPadding(0, px(4), 0, 0) }
        balanceCard.addView(costsText)
        root.addView(balanceCard)

        root.addView(section("Buy points"))
        packsBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(packsBox)

        root.addView(section("History"))
        historyBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(historyBox)

        root.addView(section("Privacy & data"))
        accountText = label("", 13f, muted).apply { setTextIsSelectable(true) }
        root.addView(accountText)
        root.addView(Button(this).apply {
            text = "Privacy notice"
            setOnClickListener { Privacy.openNotice(this@WalletActivity, wallet) }
        })
        root.addView(Button(this).apply {
            text = "Delete my data"
            setTextColor(debit)
            setOnClickListener {
                Privacy.confirmDelete(this@WalletActivity, lifecycleScope, wallet?.balance) {
                    startActivity(Intent(this@WalletActivity, MainActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NEW_TASK))
                    finish()
                }
            }
        })

        setContentView(ScrollView(this).apply { addView(root) })
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun refresh() = lifecycleScope.launch {
        try {
            val (wallet, history, tiers) = Account.call(this@WalletActivity) {
                withContext(Dispatchers.IO) {
                    Triple(
                        Api.scan.wallet(),
                        Api.scan.history(),
                        runCatching { Api.scan.tiers().tiers }.getOrDefault(emptyList()),
                    )
                }
            }
            this@WalletActivity.wallet = wallet
            accountText.text = "Account ID: ${wallet.account_id}"
            balanceText.text = "${history.balance} points"
            costsText.text = if (tiers.isEmpty()) "1 point = ₹1"
            else tiers.joinToString("   ·   ") { "${it.name} scan: ${it.points}" }
            showPacks(wallet.packs)
            showHistory(history.entries)
        } catch (e: Exception) {
            balanceText.text = "Offline"
            costsText.text = "Couldn't reach the server: ${e.message}"
        }
    }

    private fun showPacks(packs: List<PointPack>) {
        packsBox.removeAllViews()
        val best = packs.maxByOrNull { it.points }
        packs.forEach { pack ->
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(px(16), px(12), px(12), px(12))
                background = rounded(0xFFFFFFFF.toInt(), stroke = 0xFFD6DCE4.toInt())
                layoutParams = LinearLayout.LayoutParams(-1, -2).apply { topMargin = px(10) }
            }
            val texts = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
            texts.addView(label("${pack.points} points", 18f, ink, bold = true))
            val saving = savingPercent(pack)
            val sub = listOfNotNull(
                pack.price.takeIf { it.isNotBlank() },
                saving?.let { "save $it%" },
                if (pack == best && packs.size > 1) "best value" else null,
            ).joinToString("  ·  ")
            texts.addView(label(sub, 14f, if (saving != null) credit else muted))
            row.addView(texts, LinearLayout.LayoutParams(0, -2, 1f))
            row.addView(Button(this).apply {
                text = if (pack.price.isBlank()) "Buy" else "Buy ${pack.price}"
                setOnClickListener { buy(pack) }
            })
            packsBox.addView(row)
        }
    }

    /** "300 points for ₹279" saves 7% against 1 point = ₹1. */
    private fun savingPercent(pack: PointPack): Int? {
        val rupees = pack.price.filter { it.isDigit() || it == '.' }.toDoubleOrNull() ?: return null
        val pct = ((pack.points - rupees) / pack.points * 100).roundToInt()
        return pct.takeIf { it > 0 }
    }

    private fun buy(pack: PointPack) {
        if (buying) return
        val accountId = Account.id(this) ?: return toast("Account not ready yet, try again")
        buying = true
        PackBilling(this, accountId, pack.product_id) { balance ->
            buying = false
            if (balance != null) {
                toast("Added ${pack.points} points")
                refresh()
            }
        }.launch()
    }

    private fun showHistory(entries: List<LedgerEntry>) {
        historyBox.removeAllViews()
        if (entries.isEmpty()) {
            historyBox.addView(label("No transactions yet. Buy points to start scanning.", 14f, muted)
                .apply { setPadding(0, px(8), 0, 0) })
            return
        }
        val fmt = DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT)
        entries.forEach { e ->
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, px(10), 0, px(10))
            }
            val texts = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
            texts.addView(label(e.note ?: e.kind.replaceFirstChar { it.uppercase() }, 15f, ink, bold = true))
            texts.addView(label(fmt.format(Date((e.created_at * 1000).toLong())), 12f, muted))
            row.addView(texts, LinearLayout.LayoutParams(0, -2, 1f))
            row.addView(label(
                (if (e.amount >= 0) "+" else "−") + kotlin.math.abs(e.amount),
                17f, if (e.amount >= 0) credit else debit, bold = true,
            ))
            historyBox.addView(row)
            historyBox.addView(View(this).apply { setBackgroundColor(0xFFE3E7ED.toInt()) },
                LinearLayout.LayoutParams(-1, px(1)))
        }
    }

    // ---------- small view helpers ----------
    private fun px(v: Int) = (v * dp).toInt()

    private fun label(text: String, size: Float, color: Int, bold: Boolean = false) = TextView(this).apply {
        this.text = text
        textSize = size
        setTextColor(color)
        if (bold) setTypeface(typeface, Typeface.BOLD)
    }

    private fun section(title: String) = label(title, 18f, ink, bold = true).apply {
        setPadding(0, px(24), 0, px(2))
    }

    private fun rounded(fill: Int, stroke: Int) = GradientDrawable().apply {
        cornerRadius = 14 * dp
        setColor(fill)
        setStroke(px(1), stroke)
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
