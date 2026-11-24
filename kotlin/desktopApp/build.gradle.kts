import org.jetbrains.compose.desktop.application.dsl.TargetFormat

plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.compose.multiplatform)
}

val patcherVersion = rootProject.file("../VERSION")
    .takeIf { it.isFile }
    ?.readText()
    ?.trim()
    ?.removePrefix("v")
    ?.takeIf { Regex("""\d+\.\d+\.\d+""").matches(it) }
    ?: "0.2.0"

dependencies {
    implementation(project(":ui"))
    implementation(compose.desktop.currentOs)
}

compose.desktop {
    application {
        mainClass = "com.hu.patcher.desktop.MainKt"

        nativeDistributions {
            // DMG requires a MAJOR version > 0; enable it in Stage 11 together
            // with the macOS package version mapping.
            targetFormats(
                TargetFormat.Exe,
                TargetFormat.Msi,
                TargetFormat.Deb,
            )
            packageName = "hu-patcher"
            packageVersion = patcherVersion
        }
    }
}
