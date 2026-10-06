plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Override at build time:  ./gradlew :app:assembleDebug -PapiBase=https://your-api -PexportProductId=export_unlock
val apiBase: String = (project.findProperty("apiBase") as String?) ?: "https://chamber-barber-dole.ngrok-free.dev"
val exportProductId: String = (project.findProperty("exportProductId") as String?) ?: "export_unlock"

android {
    namespace = "com.scanforge.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.scanforge.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"

        buildConfigField("String", "API_BASE", "\"$apiBase\"")
        buildConfigField("String", "EXPORT_PRODUCT_ID", "\"$exportProductId\"")
    }

    buildFeatures {
        buildConfig = true
    }

    buildTypes {
        debug {
            // Allows http:// to a local backend (emulator 10.0.2.2) during development.
            manifestPlaceholders["cleartext"] = "true"
        }
        release {
            isMinifyEnabled = false
            manifestPlaceholders["cleartext"] = "false"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")

    val camerax = "1.3.4"
    implementation("androidx.camera:camera-core:$camerax")
    implementation("androidx.camera:camera-camera2:$camerax")
    implementation("androidx.camera:camera-lifecycle:$camerax")
    implementation("androidx.camera:camera-view:$camerax")

    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    implementation("com.android.billingclient:billing:7.0.0")
}
