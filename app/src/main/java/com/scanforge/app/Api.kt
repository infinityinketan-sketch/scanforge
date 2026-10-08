package com.scanforge.app

import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.ResponseBody
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Url
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
)

data class TiersResponse(val default: String?, val tiers: List<Tier>)
data class ProcessRequest(val tier: String?)
data class CreateRequest(val tier: String?)

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

    @POST("jobs/{id}/process")
    suspend fun process(@Path("id") id: String, @Body req: ProcessRequest): Response<Unit>

    @GET("tiers")
    suspend fun tiers(): TiersResponse

    @GET("jobs/{id}")
    suspend fun status(@Path("id") id: String): JobStatus

    @POST("billing/verify")
    suspend fun verify(@Body req: VerifyRequest): VerifyResponse

    @GET
    suspend fun downloadFile(@Url url: String): ResponseBody
}

object Api {
    val scan: ScanApi by lazy {
        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        }
        val client = OkHttpClient.Builder()
            // Free ngrok tunnels can answer with a browser-warning page instead of the API.
            .addInterceptor { chain ->
                chain.proceed(chain.request().newBuilder().header("ngrok-skip-browser-warning", "1").build())
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
}
