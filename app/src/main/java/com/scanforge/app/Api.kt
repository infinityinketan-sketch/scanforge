package com.scanforge.app

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.ResponseBody
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Url
import java.io.File
import java.util.concurrent.TimeUnit

data class CreateJobResponse(val job_id: String)
data class UploadResponse(val uploaded: Int)

data class JobStatus(
    val job_id: String,
    val status: String,
    val n_photos: Int,
    val error: String?,
    val paid: Boolean,
    val preview_url: String? = null,
    val download: Map<String, String>? = null,
    val tier: String? = null,
    val progress: Int? = null,
    val export_product_id: String? = null,
    val pay_before: Boolean? = null,   // true: buy before processing (service tiers)
    val price: String? = null,
    val points: Int? = null,           // points this scan costs
    val expired: Boolean? = null,      // files deleted after the retention period
)

/** A processing option offered by the server (only those with an API key configured). */
data class Tier(
    val id: String,
    val name: String,
    val detail: String,
    val min_photos: Int,
    val export_product_id: String,
    val quality: Int = 0,          // 1–5, shown as stars
    val eta: String? = null,       // e.g. "About 2 minutes"
    val price: String? = null,     // e.g. "₹99"
    val points: Int = 0,           // points this option costs
    val good_photos: Int = 0,      // photo count that gives the best result
)

data class NewAccount(val account_id: String, val token: String)
data class PointPack(val product_id: String, val points: Int, val price: String)
data class Wallet(
    val account_id: String,
    val balance: Int,
    val packs: List<PointPack>,
    val consent_version: String? = null,   // privacy notice version this account agreed to
    val privacy_version: String? = null,   // current version (null: older server, no consent step)
    val privacy_url: String? = null,
)
data class ConsentRequest(val version: String)
data class DeleteResult(val deleted: Boolean, val forfeited_points: Int)
data class LedgerEntry(
    val id: Long,
    val amount: Int,          // + credit, - debit
    val kind: String,         // purchase | scan | refund | bonus
    val ref: String?,
    val note: String?,
    val created_at: Double,   // unix seconds
)
data class History(val balance: Int, val entries: List<LedgerEntry>)
data class PackPurchase(val product_id: String, val token: String)
data class PackResult(val credited: Int, val balance: Int)

data class TiersResponse(val default: String?, val tiers: List<Tier>)
data class ProcessRequest(val tier: String?)
data class CreateRequest(val tier: String?)

data class UploadUrlsRequest(val count: Int)
/** direct=false: this server takes photos itself (multipart). Otherwise one presigned PUT per photo. */
data class UploadUrls(
    val direct: Boolean,
    val urls: List<String> = emptyList(),
    val content_type: String = "image/jpeg",
    val max_bytes: Long = 0,
)
data class PhotosComplete(val n_photos: Int, val rejected: Int = 0)

data class VerifyRequest(val job_id: String, val product_id: String, val token: String)
data class VerifyResponse(val paid: Boolean)

interface ScanApi {
    @POST("jobs")
    suspend fun createJob(@Body req: CreateRequest): CreateJobResponse

    @Multipart
    @POST("jobs/{id}/photos")
    suspend fun uploadPhotos(
        @Path("id") id: String,
        @Part photos: List<MultipartBody.Part>,
    ): Response<Unit>

    @POST("jobs/{id}/upload-urls")
    suspend fun uploadUrls(@Path("id") id: String, @Body req: UploadUrlsRequest): Response<UploadUrls>

    @POST("jobs/{id}/photos/complete")
    suspend fun photosComplete(@Path("id") id: String): PhotosComplete

    @POST("jobs/{id}/process")
    suspend fun process(@Path("id") id: String, @Body req: ProcessRequest): Response<Unit>

    @GET("tiers")
    suspend fun tiers(): TiersResponse

    @POST("accounts")
    suspend fun createAccount(): NewAccount

    @GET("account")
    suspend fun wallet(): Wallet

    @POST("account/consent")
    suspend fun consent(@Body req: ConsentRequest): Response<Unit>

    @DELETE("account")
    suspend fun deleteAccount(): DeleteResult

    @GET("account/history")
    suspend fun history(): History

    @POST("account/purchase")
    suspend fun buyPoints(@Body req: PackPurchase): PackResult

    @GET("jobs/{id}")
    suspend fun status(@Path("id") id: String): JobStatus

    @POST("billing/verify")
    suspend fun verify(@Body req: VerifyRequest): VerifyResponse

    @GET
    suspend fun downloadFile(@Url url: String): ResponseBody
}

object Api {
    /** This install's account token, set by [Account.ensure]; sent with every request. */
    @Volatile var token: String? = null

    val scan: ScanApi by lazy {
        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        }
        val client = OkHttpClient.Builder()
            // Free ngrok tunnels can answer with a browser-warning page instead of the API.
            .addInterceptor { chain ->
                val req = chain.request().newBuilder().header("ngrok-skip-browser-warning", "1")
                token?.let { req.header("Authorization", "Bearer $it") }
                chain.proceed(req.build())
            }
            .addInterceptor(logging)
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .build()
        Retrofit.Builder()
            .baseUrl(BuildConfig.API_BASE.trimEnd('/') + "/")
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ScanApi::class.java)
    }

    /**
     * Plain client for presigned file-storage links. It must not add our Authorization header:
     * the link carries its own signature, and storage rejects requests with two.
     */
    private val files: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .writeTimeout(120, TimeUnit.SECONDS)
            .build()
    }

    private fun isOurs(url: String) = url.startsWith(BuildConfig.API_BASE.trimEnd('/'))

    /** PUT a file to a presigned upload link (blocking; call from Dispatchers.IO). */
    fun putFile(url: String, file: File, contentType: String) {
        val req = Request.Builder().url(url).put(file.asRequestBody(contentType.toMediaType())).build()
        files.newCall(req).execute().use { r ->
            if (!r.isSuccessful) error("photo upload rejected (HTTP ${r.code})")
        }
    }

    /** Save a download link to [dest]: our API links go through [scan], storage links don't. */
    suspend fun download(url: String, dest: File) {
        if (isOurs(url)) {
            scan.downloadFile(url).byteStream().use { input -> dest.outputStream().use { input.copyTo(it) } }
            return
        }
        files.newCall(Request.Builder().url(url).build()).execute().use { r ->
            if (!r.isSuccessful) error("HTTP ${r.code}")
            r.body!!.byteStream().use { input -> dest.outputStream().use { input.copyTo(it) } }
        }
    }
}
