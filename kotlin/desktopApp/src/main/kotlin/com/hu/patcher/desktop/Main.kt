package com.hu.patcher.desktop

import androidx.compose.ui.window.Window
import androidx.compose.ui.window.application
import com.hu.patcher.ui.App

fun main() = application {
    Window(
        onCloseRequest = ::exitApplication,
        title = "HU Patcher",
    ) {
        App()
    }
}
