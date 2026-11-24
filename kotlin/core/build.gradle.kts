plugins {
    alias(libs.plugins.kotlin.multiplatform)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.android.library)
}

kotlin {
    androidTarget()
    jvm("desktop")

    sourceSets {
        // SSH/crypto code is JVM-only but shared by Android and desktop.
        val jvmSharedMain by creating {
            dependsOn(commonMain.get())
            dependencies {
                implementation(libs.sshj)
                implementation(libs.eddsa)
                implementation(libs.bcprov)
            }
        }
        val jvmSharedTest by creating {
            dependsOn(commonTest.get())
            dependencies {
                implementation(kotlin("test"))
            }
        }

        commonMain.dependencies {
            implementation(libs.kotlinx.coroutines.core)
            implementation(libs.kotlinx.serialization.json)
        }
        commonTest.dependencies {
            implementation(kotlin("test"))
            implementation(libs.kotlinx.coroutines.test)
        }
        androidMain.get().dependsOn(jvmSharedMain)
        val desktopMain by getting {
            dependsOn(jvmSharedMain)
        }
        val desktopTest by getting {
            dependsOn(jvmSharedTest)
        }
    }
}

android {
    namespace = "com.hu.patcher.core"
    compileSdk = 35

    defaultConfig {
        minSdk = 26
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
