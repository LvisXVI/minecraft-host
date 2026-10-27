plugins {
    id("com.android.application")
}

// Release signing comes from CI secrets; without them the release build stays unsigned.
val keystorePath: String? = System.getenv("ANDROID_KEYSTORE_PATH")

android {
    namespace = "net.lvisxvi.minecrafthost"
    compileSdk = 35

    defaultConfig {
        applicationId = "net.lvisxvi.minecrafthost"
        minSdk = 26
        // 28: phone hosting starts the bundled Java runtime from app storage, which Android only
        // allows for apps targeting API 28 or lower (the same reason Termux targets 28).
        targetSdk = 28
        versionCode = (System.getenv("ANDROID_VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("ANDROID_VERSION_NAME") ?: "1.0.0"
    }

    signingConfigs {
        if (keystorePath != null) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystorePath != null) signingConfig = signingConfigs.getByName("release")
        }
    }

    lint {
        // targetSdk 28 is deliberate (see defaultConfig); the app is distributed outside Google Play.
        disable += setOf("ExpiredTargetSdkVersion", "OldTargetApi")
    }

    androidResources {
        // jre.zip is already compressed
        noCompress += "zip"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
