plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.scanforge.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.scanforge.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1"
        // Set your backend URL:  ./gradlew assembleDebug -PapiBase=https://YOUR-API.onrender.com
        buildConfigField(
            "String", "API_BASE",
            "\"${project.findProperty("apiBase") ?: "https://YOUR-BACKEND.onrender.com"}\""
        )
    }

    buildFeatures { buildConfig = true }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.1")
    implementation("androidx.camera:camera-core:1.3.4")
    implementation("androidx.camera:camera-camera2:1.3.4")
    implementation("androidx.camera:camera-lifecycle:1.3.4")
    implementation("androidx.camera:camera-view:1.3.4")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("com.android.billingclient:billing-ktx:7.1.0")
}
