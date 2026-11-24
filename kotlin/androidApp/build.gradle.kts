plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.compose.multiplatform)
}

val patcherVersion = rootProject.file("../VERSION")
    .takeIf { it.isFile }
    ?.readText()
    ?.trim()
    ?.removePrefix("v")
    ?.ifBlank { "0.0.0" }
    ?: "0.0.0"

android {
    namespace = "com.hu.patcher.android"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.hu.patcher"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = patcherVersion
    }

    buildFeatures {
        compose = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
        resources.excludes += "META-INF/versions/9/OSGI-INF/MANIFEST.MF"
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(project(":ui"))
    implementation(libs.androidx.activity.compose)
    implementation(compose.runtime)
    implementation(compose.ui)
}
