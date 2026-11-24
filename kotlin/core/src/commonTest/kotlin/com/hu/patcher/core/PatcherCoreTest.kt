package com.hu.patcher.core

import kotlin.test.Test
import kotlin.test.assertTrue

class PatcherCoreTest {
    @Test
    fun versionIsSet() {
        assertTrue(PatcherCore.VERSION.isNotBlank())
    }
}
